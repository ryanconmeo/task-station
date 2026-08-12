#!/usr/bin/env python3
"""Task Station — persistent, cross-session task tracking for Claude Code.

Engine for the /todo and /done commands and the SessionStart / UserPromptSubmit
hooks. Tasks live as one JSON file per task under store/tasks/. A session is
"attached" to at most one task via a link file under store/links/<session_id>.

Subcommands:
  create  --session ID --title T --summary S   create a task, attach the session
  attach  --session ID --task REF              attach session to an existing task
  bump    --session ID                          touch the attached task's activity
  skip    --session ID                          mark session intentionally untracked (silences nudge)
  done    --session ID                          close the attached task
  render  --session ID --arg STR                /todo entrypoint (list | detail+attach)
  prompt-context --session ID                   UserPromptSubmit hook context
  session-start  --session ID --source SRC      SessionStart hook context
  guidance                                      full attach/create how-to (on demand)

REF is a 1-based index from the most recent `render` listing, or a task id /
id-prefix. All writes are atomic (temp file + os.replace).
"""

import argparse
import atexit
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import decisions as _dec
import heal as _heal
import hook_health
import knowledge as _knowledge
import paths
import save as _save
import steps as _steps
import store

BASE = os.path.dirname(os.path.abspath(__file__))  # code location only (self-invocation)


def _cli_fallback():
    """Parenthetical fallback for the short `task-station <cmd>` form we now show
    in model-facing guidance/help. Claude Code puts the plugin's bin/ on the Bash
    tool PATH while the plugin is enabled, so `task-station` runs this engine; this
    names the absolute python3 form for shells where bin/ isn't on PATH."""
    return ("(`task-station` resolves via the plugin's bin/ on PATH; if it isn't, "
            "run: python3 %s/task-station.py …)" % BASE)


DATA = paths.data_dir()                             # mutable state — survives /plugin update
STORE = os.path.join(DATA, "store")
TASKS_DIR = os.path.join(STORE, "tasks")
LINKS_DIR = os.path.join(STORE, "links")
PENDING_BRIEFS = os.path.join(DATA, "pending-briefs")
DELEGATE_REGISTRY = os.path.join(DATA, "workers.json")
_LIVE_BG_INDEX = None   # per-process snapshot of `claude agents --json` (bg-aware resume); None = not yet queried
PROJECTS_ROOT = os.path.join(
    os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")), "projects")

LOG_KEEP = 25          # max activity-log entries kept per task
EVENTS_KEEP = 100      # max append-only per-task event-feed entries kept (the delta-brief source)
EVENT_TEXT_MAX = 160   # max chars stored per event text (privacy + injection-budget cap)
# DECISIONS ARE NOT TRUNCATED. Every still-current decision renders in the default
# detail — no age limit, no count limit, no "+N earlier" pointer. The one thing that
# keeps a decision off this surface is no longer being TRUE (superseded / split /
# merged), which is `decisions.digest_order`'s job. The removed `DECISIONS_TAIL = 6`
# selected by age, a proxy for load-bearing-ness and a wrong one: it hid valid old
# decisions and showed invalid recent ones.
DECISION_PIN_MARK = "★ "        # marks a pinned decision (now: sorts FIRST) wherever decisions render
DECISION_DEAD_MARK = "⊘ "       # marks a superseded decision (history view ONLY)
ACTIVITY_TAIL = 8      # recent-activity entries rendered inline in the default detail
FILES_KEEP = 15        # max recently-edited file paths kept per task (most-recent-last)
NUDGE_PROMPT_MAX = 120  # chars of the prompt stored in the activity log
NUDGE_ESCALATE_AFTER = 4   # unattached prompts before the nudge escalates
SKIP_SENTINEL = "__skip__"  # link value marking a session intentionally untracked
MAX_CLOSED_IN_LIST = 5  # closed tasks shown in the /todo list (most recent first)
SUBSTANCE_FLOOR = 3     # min user messages for a session to count as "real working" work
IDLE_GAP_CAP = 1800     # >30min between activity bumps starts a NEW time span (the gap is not counted)
SPANS_KEEP = 200        # max stored [start, end] activity spans per task (append-capped, most-recent kept)
SEARCH_SCAN_LIMIT = 100  # ranked hits pulled from the store before status-filtering
SEARCH_HITS_SHOWN = 10   # tier-1 hits rendered (token-economical ranked list)
DELTA_MAX_ITEMS = 6      # max events surfaced in one injection-time delta brief
DELTA_MAX_CHARS = 700    # hard char cap on a single delta brief block

# Memo correspondence — a fact/decision handed to a task's working session(s). The
# body lives in task["memos"] (NOT the event feed, whose EVENT_TEXT_MAX privacy cap
# + EVENTS_KEEP trim would destroy an unacked memo); the feed carries only a preview.
# The body itself is UNCAPPED — a memo is inter-session correspondence and must
# arrive whole (a silent truncation once cut a design spec mid-sentence); the
# injection surfaces stay budgeted via the preview caps below.
MEMOS_KEEP     = 50     # trim target: oldest FULLY-ACKED memos beyond this
MEMOS_HARD_CAP = 200    # safety: past this, trim oldest regardless of acks
MEMO_PENDING_MAX = 3    # pending-brief lines per injection
MEMO_LINE_MAX  = 200    # preview chars per pending-brief line

# CORRECTION-LANGUAGE SAFETY NET. `--corrects` only helps when the sender remembers
# to declare it; a memo that announced a permission-model change was once acked
# without the correction ever reaching the durable layer, and two workers were then
# briefed to do something structurally impossible. So a memo whose BODY reads like a
# correction but declares no `--corrects` target gets flagged: a warning to the sender
# at send time (never a block — the sender may have good reason), and a prominent
# reminder to the acker to go update the durable stores. Edit the list HERE — it is the
# one place these live.
CORRECTION_PATTERNS = ("correction", "supersede", "retraction", "withdrawn",
                       "no longer", "stop doing")

# Which of those patterns are NOUNS. The word standing in front of a pattern means
# something different depending on its part of speech, which is why the two groups are
# read with different vocabularies — see `heal.NOUN_DECLARING_QUALIFIERS`.
CORRECTION_NOUN_PATTERNS = ("correction", "retraction")


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

# Task lifecycle is ONE field — `status` — with three values:
#   open (○)  →  active (●)  →  closed
# A topic merely *raised* starts `open` and shows on the board immediately; it
# graduates to `active` when work actually starts (delegate --worktree, a file
# edit in an attached session, the manual `status` command, or `create --active`);
# /done closes it. "On the board / not done" means status in {open, active};
# "is closed" stays status == "closed". A missing/unknown status reads as open
# (back-compat — pre-existing tasks were open/closed only).
STATUS_OPEN = "open"
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
STATUS_DEFAULT = STATUS_OPEN
STATUS_BOARD = (STATUS_OPEN, STATUS_ACTIVE)   # "not closed" — on the board
STATUS_SETTABLE = (STATUS_OPEN, STATUS_ACTIVE)  # the manual `status` command's range
STATUS_GLYPH = {STATUS_OPEN: "○", STATUS_ACTIVE: "●"}
# Closed marker for the Markdown board's status column (✕ U+2715). The ASCII list
# still mutes closed to a blank placeholder via status_glyph(); this is the
# Markdown-table mapping only: ● active · ○ new · ✕ closed.
STATUS_GLYPH_CLOSED = "✕"

# The stored status value `open` DISPLAYS as "new" everywhere user-facing (the
# per-task state is New ○ · Active ● · Closed ✕). The internal value stays `open`
# — no migration, full back-compat — and the not-closed board SECTION keeps the
# name "Open" (it groups New + Active); only the per-task label changes.
STATUS_DISPLAY = {STATUS_OPEN: "new", STATUS_ACTIVE: "in progress", STATUS_CLOSED: "closed"}
# `new` is the INPUT ALIAS for the stored `open`, accepted wherever `open` is (the
# `status`/`create` paths) so a user can type the displayed word back in.
STATUS_INPUT_ALIASES = {"new": STATUS_OPEN}


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

# Categories / colours are an OPTIONAL plugin: all of that logic lives in
# categories.py. If it's absent (or fails to import), `cats` is None and the
# tracker runs plain and colourless — no tags, no --color, no tint hints. See
# categories.py and CATEGORIES.md.
try:
    import categories as cats
except Exception:
    cats = None


def cat_color(color):
    """Normalised colour to store on a task, or None when categories are off."""
    return cats.normalize(color) if cats else None


def cat_tag(color, pad=False):
    """`<emoji> [TAG]` for the list, or "" when categories are off."""
    return cats.tag(color, pad=pad) if cats else ""


def task_status(task):
    """A task's lifecycle status, defaulting a missing/unknown value to open —
    so tasks written before this field existed read as open (back-compat)."""
    s = (task or {}).get("status")
    return s if s in (STATUS_OPEN, STATUS_ACTIVE, STATUS_CLOSED) else STATUS_DEFAULT


def is_closed(task):
    """True iff the task is done (status == closed)."""
    return task_status(task) == STATUS_CLOSED


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


# ----------------------------------------------------------------- effort ----
# Optional per-task effort estimate (complexity / scope), shown as a column in
# the /todo list. Canonical t-shirt sizes; a 5-segment filled bar makes the
# column scannable at a glance — count of filled segments (not bar height) is
# the size cue, which reads instantly even on a single row. Stored on the task
# as one of EFFORT_ORDER, or absent.
EFFORT_ORDER = ["XS", "S", "M", "L", "XL"]
_EFFORT_SLOTS = len(EFFORT_ORDER)
# filled ▰ to (index+1), empty ▱ for the rest → ▰▱▱▱▱ (XS) … ▰▰▰▰▰ (XL)
EFFORT_GAUGE = {
    s: "▰" * (i + 1) + "▱" * (_EFFORT_SLOTS - i - 1)
    for i, s in enumerate(EFFORT_ORDER)
}
EFFORT_GAUGE_EMPTY = "▱" * _EFFORT_SLOTS  # placeholder when effort is unset
EFFORT_WORD = {"XS": "trivial", "S": "small", "M": "medium", "L": "large", "XL": "huge"}
_EFFORT_ALIASES = {
    "xs": "XS", "tiny": "XS", "trivial": "XS", "1": "XS",
    "s": "S", "small": "S", "2": "S",
    "m": "M", "med": "M", "medium": "M", "3": "M",
    "l": "L", "large": "L", "big": "L", "4": "L",
    "xl": "XL", "huge": "XL", "epic": "XL", "5": "XL", "xxl": "XL",
}


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


# --------------------------------------------------------- ultracode fan-out ----
# "ultracode" is Claude Code's built-in multi-agent Workflow / dynamic-
# orchestration feature. Task Station never fires it — it only HINTS, and only
# when a task genuinely warrants breadth, and only for read/analyze/design/review
# phases (never repo writes, never trivial work).
#
# Categories whose NATURE warrants multi-agent breadth even at medium effort.
# Referenced by category KEY (the slot identifier) so this stays correct if a
# tag/label is re-skinned: orange = REVIEW, purple = RESEARCH, brown = DATA
# (databases / schemas / ETL / migrations slot).
FANOUT_CATEGORIES = ("orange", "purple", "brown")
_FANOUT_ANY_MIN = "L"       # effort at/above which ANY category is fan-out-worthy
_FANOUT_BREADTH_MIN = "M"   # effort at/above which a breadth category qualifies

_ULTRACODE_RE = re.compile(r"\bultracode\b", re.I)


def fanout_worthy(task):
    """True when a task genuinely warrants multi-agent BREADTH (the ultracode
    fan-out hint). PURE and DERIVED — reads only the task's effort + category and
    adds no task state, so it can be recomputed anywhere. TRUE when effort is L/XL
    (any category), OR the category is one of the breadth slots
    (REVIEW/RESEARCH/DATA) at M+. FALSE for xs/s, an unset/unknown effort, or
    a plain open (○) question / untracked task (no effort)."""
    eff = task.get("effort") if isinstance(task, dict) else None
    if eff not in EFFORT_ORDER:          # unset / unknown effort → never worthy
        return False
    i = EFFORT_ORDER.index(eff)
    if i >= EFFORT_ORDER.index(_FANOUT_ANY_MIN):     # L / XL — any category
        return True
    color = task.get("color")
    key = cats.resolve(color) if (cats and hasattr(cats, "resolve")) else color
    return key in FANOUT_CATEGORIES and i >= EFFORT_ORDER.index(_FANOUT_BREADTH_MIN)


def ultracode_signal(prompt):
    """True when this turn carries an ultracode opt-in token: the word-boundary
    token `ultracode` (case-insensitive) anywhere in the prompt — the SAME trigger
    Claude Code's harness uses to switch on multi-agent orchestration.

    This is the ONLY reliable per-turn signal available to the UserPromptSubmit
    hook. The hook input (and TASK_STATION_PROMPT) carry no 'standing ultracode
    mode' field or env var, so we deliberately do NOT attempt to detect a standing
    mode here — inventing a fragile detector would mis-steer the model. Standing-
    mode users still get the human advisory; only the model-facing steering is
    keyed to this token. See the design spec."""
    return bool(prompt) and bool(_ULTRACODE_RE.search(prompt))


def ultracode_advisory(task):
    """The HUMAN-facing fan-out advisory (default mode): suggest the human opt into
    Claude Code's `ultracode` multi-agent breadth for a worthy task's read/analyze/
    design/review phases. The human opts in by typing the keyword — this NEVER
    fires orchestration and NEVER suggests pointing a workflow at repo writes.
    Returns '' unless the task is fan-out-worthy AND ultracode hints are enabled."""
    import config
    if not (config.ultracode_hints_enabled() and fanout_worthy(task)):
        return ""
    return ("ultracode: this task is fan-out-worthy (effort %s). For its "
            "read/analyze/design/review phases, running it with `ultracode` gives "
            "multi-agent breadth (if your Claude Code supports it). Repo edits still "
            "go through delegation (worktree + story/PR) — never point a workflow at "
            "writes." % task.get("effort"))


def ultracode_steering():
    """The MODEL-facing steering block, printed by the per-prompt hook ONLY on an
    ultracode turn (signal present) for a fan-out-worthy attached task. The harness
    is ALREADY orchestrating; this just steers breadth to think-phases and keeps
    every repo write on the sanctioned delegation path (worktree + story/PR)."""
    return ("ultracode active on a fan-out-worthy task: fan subagents out for "
            "read/analyze/design/review/verify ONLY (hub context — no repo "
            "CLAUDE.md/hooks/build env). Route every repo MUTATION through "
            "task-station delegation (a worktree worker off the repo's base branch, "
            "with story + PR). Never edit/build/test in workflow subagents.")


# Authoritative command help — git/fd/gh style aligned block. ONE source of truth
# for both surfaces: the ASCII list footer (commands_footer) and the Markdown
# board footer (commands_footer_md, which fences it so it stays monospace). The
# description column is auto-aligned to the widest command + a 4-space gutter.
_COMMANDS_HELP = [
    ("/todo",                "show the board"),
    ("/todo <n>",            "open & resume a task"),
    ("/todo <n> history",    "full trace: decisions + log + activity"),
    ("/todo <n> prompts",    "exact prompt trail (timestamped, per session)"),
    ("/todo <n1, n2, …> -s", "jump into task session(s), in a new window"),
    ("/todo closed [N]",     "list recent closed (default 20)"),
    ("/todo all",            "show every task (all open + closed)"),
    ("/todo search <terms>", "search all tasks (add --open/--closed)"),
    ("/todo board",          "open the visual HTML board"),
    ("/todo save",           "checkpoint the current task for a seamless resume"),
    ("/todo heal",           "reconcile the decision log into current state (dry run)"),
    ("/todo pin",            "pin this session as the task's resume target"),
    ("/todo done [n,…]",     "close the current task (or by number)"),
    ("/todo config [flags]", "open settings"),
]
_COMMANDS_LEGEND = "<n> a task number  ·  <n1, n2, …> one or more  ·  [N] optional count"


def _command_label(label, bare):
    """The label shown for one command. When `bare` is False, rewrite the LEADING
    bare token (/todo, /done, /pin) to its /task-station: form, preserving any
    trailing args (e.g. "/todo <n>" → "/task-station:todo <n>"); an already-
    namespaced token is untouched. When True, return the label as-is. Mirrors
    tools/render_board.py's _command_label so the terminal footer and the HTML
    board agree on how bare-state is shown."""
    if bare:
        return label
    for tok in ("/todo", "/done", "/pin"):
        if label == tok or label.startswith(tok + " "):
            return "/task-station:" + tok[1:] + label[len(tok):]
    return label


def _bare_commands():
    """config.bare_commands(), defaulting to False (the config default) when
    config is unavailable — the same guarded-import pattern used elsewhere in
    this file (e.g. write_board())."""
    try:
        import config
        return config.bare_commands()
    except Exception:
        return False


def _commands_footer_note(bare):
    """The bare-state note appended under the Commands footer — mirrors the HTML
    board's helpnote (tools/render_board.py's _help_panel) so both surfaces agree
    on how to read/change the bare-cmds setting. TWO lines (joined by a newline) so
    the /task-station: prefix statement always stands on its own line."""
    if bare:
        return ("bare-cmds is on — /todo, /done, /save, /heal, /pin, /history, /repos work directly.\n"
                "The /task-station: prefix also always works.")
    return ("bare-cmds is off — use the /task-station: prefix (shown).\n"
            "Enable the short /todo, /done, /save, /heal, /pin, /history, /repos aliases with "
            "/task-station:config --bare-cmds on.")


def _commands_help(bare):
    """The aligned command/description lines + the notation legend (no code fence).
    Column auto-sized to the widest LABEL (after the bare/namespaced rewrite) so
    the descriptions still line up in either state."""
    labeled = [(_command_label(cmd, bare), desc) for cmd, desc in _COMMANDS_HELP]
    w = max(len(cmd) for cmd, _ in labeled) + 4
    lines = ["%s%s" % (cmd.ljust(w), desc) for cmd, desc in labeled]
    return "\n".join(lines) + "\n\n" + _COMMANDS_LEGEND


def commands_footer():
    """The authoritative `/todo` command help as an aligned block (ASCII list
    footer), bare-aware like the HTML board's Commands panel: labels show their
    /task-station: form unless config.bare_commands() is on, followed by a note
    on how to read/change that state. Same command+legend content as
    commands_footer_md(), minus the Markdown code fence."""
    bare = _bare_commands()
    return _commands_help(bare) + "\n\n" + _commands_footer_note(bare)


def commands_footer_md():
    """The same aligned command help, under a **Commands** heading and wrapped in a
    fenced code block so it renders verbatim/monospace in the Markdown `/todo`
    board and the README; the bare-state note sits outside the fence (prose, not
    command syntax). Decoupled from the ASCII one-liner — built from the shared
    _COMMANDS_HELP source, not by splitting commands_footer()."""
    bare = _bare_commands()
    return ("**Commands**\n\n```\n" + _commands_help(bare) + "\n```\n\n"
            + _commands_footer_note(bare))


# ---------------------------------------------------------------- storage ----
#
# The read/write layer lives in store.py — a SQLite backend (`<store>/tasks.db`)
# when sqlite3 is available, the original file-per-task JSON store as a fallback.
# The functions below keep their historical names/signatures so call sites (and
# the tests) don't change; each just delegates to the active backend. STORE is a
# module global the tests repoint, so resolve the backend per call against it.


def _backend():
    return store.get_backend(STORE)


def _ensure_dirs():
    _backend().ensure()


def _now():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _iso_to_ts(s):
    """Epoch seconds for an ISO string written by _iso, or None if unparseable."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def _local_iso(s):
    """An ISO timestamp written by _iso (UTC), converted to the system local
    timezone for display. Naive strings are treated as UTC. Returns the input
    unchanged if it can't be parsed, so old/foreign values render as-is."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def load_task(task_id):
    return _backend().load_task(task_id)


RevConflict = store.RevConflict


def save_task(task, expected_rev=None):
    task["updated_at"] = _iso(task["updated_ts"])
    _backend().save_task(task, expected_rev=expected_rev)


def mutate(task_id, mutator_fn, retries=5):
    """Optimistic read-modify-write: load `task_id`, apply `mutator_fn(task)` in
    place, save guarded by the loaded rev, and on a concurrent-writer conflict
    reload + re-run the mutator (up to `retries`). THE required path for any
    concurrent task mutation — two writers appending to the same feed both survive
    instead of one clobbering the other (see CONTRIBUTING).

    `mutator_fn` MUST be pure (transform the given dict only; no I/O, no save, no
    reads of mutable external state) since a conflict re-runs it on fresh state.
    Returns the saved task (None if it doesn't exist)."""
    def _wrapped(t):
        mutator_fn(t)
        if t.get("updated_ts") is not None:
            t["updated_at"] = _iso(t["updated_ts"])
    return _backend().mutate(task_id, _wrapped, retries=retries)


def create_with_seq(task):
    """Transactionally allocate a unique seq and persist `task` in one step —
    the ONLY correct way to mint a new task's number. Replaces the racy
    ensure_seqs()+_max_seq()+1 read-then-write: two concurrent creators can never
    land on the same seq (BEGIN IMMEDIATE + a UNIQUE(seq) index + retry). Returns
    the task with its assigned `seq`; callers may keep mutating and save_task()."""
    task["updated_at"] = _iso(task["updated_ts"])
    return _backend().create_with_seq(task)


def delete_task(task_id):
    """Remove a task from the store entirely. Used to GC an untouched provisional
    auto-task (Workstream D) when its session is skipped or closed."""
    _backend().delete_task(task_id)


def all_tasks():
    return _backend().all_tasks()


def search_tasks(query, limit=50):
    """Ranked cross-task search via the active backend (FTS5 index, or the LIKE
    fallback). Returns [{"id", "snippet", "score"}, …] best-first; the caller loads
    + filters + renders. Defensive — a store/search hiccup returns no hits rather
    than breaking the command."""
    try:
        return _backend().search(query, limit)
    except Exception:
        return []
# ------------------------------------------------------- Obsidian export hook ---
# Opt-in, one-way mirror of a task into an Obsidian vault (lib/obsidian_sync.py).
# Guarded end-to-end: OFF unless a vault is configured, and EVERY call is wrapped
# so an export failure (missing vault, permission error, a bug) degrades to a
# stderr warning and NEVER breaks the engine mutation that triggered it.

def _obsidian_vault():
    """The configured vault path, or None when export is off. Cheap to call on the
    hot mutation path — a bare config read."""
    try:
        import config
        return config.obsidian_vault() or None
    except Exception:
        return None


def _owner():
    """The configured shared-vault owner handle, or None when unset (single-owner,
    BYTE-IDENTICAL to today). Cheap config read on the mutation path."""
    try:
        import config
        return config.owner() or None
    except Exception:
        return None


def _obsidian_perm_marker():
    """Path to the one-shot marker that dedupes the permission hint across the many
    separate CLI processes of one episode. Lives in the mutable data dir (resolved
    fresh so test isolation via TASK_STATION_HOME is honoured)."""
    return os.path.join(paths.data_dir(), ".obsidian-perm-warned")


def _obsidian_persistent_help(vault):
    """The actionable multi-line remedy text for a PERSISTENT export failure — one a
    hot-path mutation AND the unsandboxed end-of-turn hook flush both failed to write.
    Frames the cause (protected folder or gone path) and the four fixes. Shared by the
    deduped hot-path warning and the manual `obsidian --flush` summary."""
    try:
        import obsidian_sync
        protected = obsidian_sync.is_protected_vault_path(vault)
    except Exception:
        protected = False
    where = " under a macOS-protected folder" if protected else ""
    return (
        "task-station: Obsidian export keeps FAILING even from an unsandboxed "
        "auto-flush%s: %s\n"
        "  Tasks remain pending-resync — the vault is drifting stale. Fix any one of:\n"
        "    (a) grant Claude Code / your terminal Full Disk (or Documents) access "
        "in System Settings > Privacy & Security;\n"
        "    (b) point the vault OUTSIDE a protected folder: "
        "/task-station:config --obsidian-vault \"~/ObsidianVault\";\n"
        "    (c) enable instant inline exports: "
        "task-station config --obsidian-sandbox on;\n"
        "    (d) or run: task-station obsidian --flush (or --sync-all) from an "
        "unsandboxed shell.\n" % (where, vault))


def _warn_obsidian_persistent_once(vault):
    """Print ONE actionable hint per episode for a persistent failure, then a marker
    file dedupes every later mutation (each is its own process, so an in-memory flag
    wouldn't hold). Cleared on the next successful export so a future breakage
    re-warns. Best-effort — a marker read/write hiccup must never break a mutation."""
    marker = _obsidian_perm_marker()
    try:
        if os.path.exists(marker):
            return
    except Exception:
        pass
    sys.stderr.write(_obsidian_persistent_help(vault))
    try:
        with open(marker, "w") as f:
            f.write("obsidian export persistently failing — see stderr hint\n")
    except Exception:
        pass


def _clear_obsidian_perm_marker():
    """Drop the one-shot marker once an export succeeds, so a LATER permission
    breakage warns afresh. Best-effort no-op when absent/unwritable."""
    try:
        os.remove(_obsidian_perm_marker())
    except OSError:
        pass


def _obsidian_note_data(task):
    """(usage_dict_or_None, prompts_list_or_None) for a task's vault note — the
    derived Usage block + (opt-in) Prompts trail. Usage is read from the persisted
    ledger (cheap, no transcript scan); the prompt trail is passed through ONLY when
    the opt-in --obsidian-prompts config is on (a synced vault may leave the
    machine). Fully guarded — any failure degrades to (None, None) so the note
    still writes and the mutation never breaks."""
    try:
        import config as _cfg, export as _export
        include = {"usage"}
        if _cfg.obsidian_prompts_enabled():
            include.add("prompts")
        _usage_engine()   # point usage's path globals at the frozen engine paths
        return _export.note_context(_backend(), task, include)
    except Exception:
        return None, None


def _related_pairs(task, scan, knowledge, stem_of):
    """The render_note `related` list for one task — shared by the generic export and
    the Obsidian mirror so the two paths carry the SAME graph (one renderer, byte
    parity). UNIVERSAL task↔task edges (stored lineage + derived semantic
    `touches-same`) are ALWAYS included as resolvable `(stem, title, kind)` triples;
    cited-note co-citation `(slug, "knowledge")` pairs are added ONLY when `knowledge`
    (the knowledge-graph gate) is on — so a gate-off render leaks zero knowledge.

    `stem_of(task_id)` maps a related task to its note STEM in the target dir (None ⇒
    that task has no note there, so the edge is dropped — every emitted stem
    resolves). Pure over `scan`; deterministic order (out lineage, reverse lineage,
    touches-same strongest-first, then sorted cited notes)."""
    title_by_id = {t.get("id"): t.get("title") for t in scan}
    pairs, seen = [], set()

    def _add_task(other_id, kind):
        if not other_id or other_id in seen:
            return
        stem = stem_of(other_id)
        if not stem:
            return
        seen.add(other_id)
        pairs.append((stem, title_by_id.get(other_id) or "", kind))

    edges = related_edges(task, scan, semantic=True)
    for e in edges.get("out", []):
        _add_task(e.get("id"), e.get("kind") or "related")
    for e in edges.get("in", []):
        _add_task(e.get("id"), "spawned" if e.get("kind") == "spawned-from" else "related")
    for e in edges.get("semantic", []):
        _add_task(e.get("id"), "touches same")
    if knowledge:
        for slug in sorted(_task_cited_notes(task)):
            if slug not in seen:
                seen.add(slug)
                pairs.append((slug, "knowledge"))
    return pairs


def _knowledge_gate_on():
    """Whether the second-brain co-citation tier is enabled (knowledge-graph flag).
    Guarded — a config hiccup reads as off."""
    try:
        import config as _cfg
        return bool(_cfg.knowledge_graph_enabled())
    except Exception:
        return False


def _export_related_fn(task, scan, stem_of):
    """The `related_fn` the generic export passes to export.export_tasks: universal
    task edges always, co-citation only when the knowledge-graph gate is on, with
    stems resolved against the export dir (via `stem_of`). Thin wrapper over
    _related_pairs so the export and the vault mirror share one graph. The category-hub
    link is appended, then the orthogonal grouped-story links (each when its toggle is on)."""
    pairs = _related_pairs(task, scan, _knowledge_gate_on(), stem_of)
    return _with_story_hubs(_with_category_hub(pairs, task, _owner()), task, _owner())


def _category_hubs_on():
    """Whether the category-hubs tier is enabled (default ON). Guarded — a config
    hiccup reads as off (no link, no hub pages), never crashing an export."""
    try:
        import config as _cfg
        return bool(_cfg.obsidian_category_hubs_enabled())
    except Exception:
        return False


def _subgroups_on():
    """Whether emergent sub-groups are active — the subgroups toggle (default ON) AND
    the category-hubs toggle (nesting: a within-category sub-hub is meaningless without
    the category hub). Guarded — a config hiccup reads as off."""
    try:
        import config as _cfg
        return _category_hubs_on() and bool(_cfg.obsidian_subgroups_enabled())
    except Exception:
        return False


def _story_groups_on():
    """Whether story hubs are active — the story-groups toggle (default ON) AND the
    category-hubs toggle (nesting, mirroring sub-groups). Guarded — a config hiccup
    reads as off. Stories are an ORTHOGONAL axis to categories, but the toggle nests
    under category hubs so one switch turns the whole clustering layer off."""
    try:
        import config as _cfg
        return _category_hubs_on() and bool(_cfg.obsidian_story_groups_enabled())
    except Exception:
        return False


# The full-board sub-group token map ({task_id: token}) for the CURRENT sync/export
# operation. Set by _begin_subgroups() around a multi-task loop so the per-note link
# resolves in O(1) and is consistent with the pages; None ⇒ resolve lazily per task.
_SUBGROUP_MAP = None

# The full-board GROUPED story ids (a set) for the CURRENT sync/export operation —
# story ids referenced by >= 1 tasks, so the per-note [[stories/<id>]] link is added
# only for stories that actually get a hub. Set by _begin_subgroups(); None ⇒ lazy.
_STORY_GROUP_IDS = None


def _compute_subgroup_token_map(tasks):
    """`{task_id: token}` for every task in `tasks` that belongs to an emergent
    sub-group in its category, over the whole given task set. Groups tasks by their
    resolved category key (matching the disk hubs), tokenises title+slug via
    categories.detect_subgroups, and inverts the result. Empty/guarded — subgroups are
    a best-effort refinement and must never break an export."""
    try:
        import categories, obsidian_sync
        by_cat = {}
        for t in tasks:
            key = categories.normalize((t or {}).get("color") or "")
            by_cat.setdefault(key, []).append(
                {"id": t.get("id"), "title": t.get("title") or "",
                 "slug": obsidian_sync.slugify(t.get("title"))})
        groups = categories.detect_subgroups(by_cat)
        return {m["id"]: token
                for toks in groups.values()
                for token, members in toks.items()
                for m in members}
    except Exception:
        return {}


def _compute_story_group_ids(tasks):
    """The set of story ids referenced by >= 1 of `tasks` — the ids that get a story
    hub, keyed identically to the emitted `stories/<id>.md` pages (via
    obsidian_sync.story_ref over each task's STRUCTURED `stories` field, never title
    tokens). A task counts once per distinct id. Guarded — story groups are a
    best-effort refinement and must never break an export."""
    try:
        import obsidian_sync
        counts = {}
        for t in tasks:
            ids = set()
            for ent in merged_stories(t):
                sid, _ = obsidian_sync.story_ref(ent)
                if sid:
                    ids.add(sid)
            for sid in ids:
                counts[sid] = counts.get(sid, 0) + 1
        return {sid for sid, c in counts.items() if c >= 1}
    except Exception:
        return set()


def _begin_subgroups():
    """Compute the full-board sub-group map AND the grouped-story-id set ONCE for a
    multi-task sync/export, so the per-note link resolution below is O(1) and
    byte-consistent with the emitted pages. A no-op (empty) when the respective toggle
    is off. Paired with _end_subgroups(). (Named for sub-groups historically; it now
    also preps the orthogonal story axis, which brackets the same operations.)"""
    global _SUBGROUP_MAP, _STORY_GROUP_IDS
    tasks = all_tasks()
    _SUBGROUP_MAP = _compute_subgroup_token_map(tasks) if _subgroups_on() else {}
    _STORY_GROUP_IDS = _compute_story_group_ids(tasks) if _story_groups_on() else set()


def _end_subgroups():
    global _SUBGROUP_MAP, _STORY_GROUP_IDS
    _SUBGROUP_MAP = None
    _STORY_GROUP_IDS = None


def _subgroup_token_for(task):
    """The emergent sub-group token this task belongs to within its category, or None.
    Reads the active per-operation map when set (a full sync/export), else computes it
    lazily over the full board (a single-task mirror). None when subgroups are off."""
    if not _subgroups_on():
        return None
    tid = (task or {}).get("id")
    m = _SUBGROUP_MAP if _SUBGROUP_MAP is not None else _compute_subgroup_token_map(all_tasks())
    return m.get(tid)


def _category_hub_pair(task, owner=None):
    """The `## Related` pair linking a task note to its MOST-SPECIFIC hub:
    `(target, LABEL, None)` → `[[<target>|<LABEL>]]` (a 3-tuple with no ' — kind' suffix).
    `target` is `categories/<slug>` (the category hub) — or, when the task belongs to an
    emergent sub-group (WS11), the nested `categories/<slug>/<token>` sub-hub, with LABEL
    the uppercased token instead of the category [TAG]. An `<owner>/` prefix scopes it to
    a shared vault so each owner resolves to their OWN subtree. `slug`/`TAG` come from the
    task's category (uncategorised ⇒ the default, e.g. `general`/`GENERAL`). None when the
    category-hubs toggle is off — a gate-off render carries no category link. Guarded —
    any failure ⇒ None."""
    if not _category_hubs_on():
        return None
    try:
        import categories
        meta = categories.hub_meta((task or {}).get("color") or "")
        base = "categories/%s" % meta["slug"]
        label = meta["tag"]
        token = _subgroup_token_for(task)
        if token:
            base = "%s/%s" % (base, token)
            label = token.upper()
        if owner:
            base = "%s/%s" % (owner, base)
        return (base, label, None)
    except Exception:
        return None


def _with_category_hub(pairs, task, owner=None):
    """Append the category-hub `## Related` pair to an already-built related list (when
    the toggle is on). Shared by the vault mirror and the generic export so both carry
    the SAME category link. Returns the (possibly extended) list."""
    pair = _category_hub_pair(task, owner)
    return (list(pairs) + [pair]) if pair else pairs


def _story_hub_pairs(task, owner=None):
    """The `## Related` pairs linking a task note to each GROUPED story hub it references:
    `(target, "Story <id>", None)` → `[[<target>|Story <id>]]`, `target` = `stories/<slug>`
    (owner-prefixed for a shared vault). One per distinct grouped story id (referenced by
    >= 1 tasks); [] when the story-groups toggle is off or the task cites no grouped story.
    Cross-category — added IN ADDITION to the category pair. The grouped-id set comes from
    the active per-operation map when set (a full sync/export), else is computed lazily.
    Guarded — any failure ⇒ []."""
    if not _story_groups_on():
        return []
    try:
        import obsidian_sync
        grouped = (_STORY_GROUP_IDS if _STORY_GROUP_IDS is not None
                   else _compute_story_group_ids(all_tasks()))
        out, seen = [], set()
        for ent in merged_stories(task):
            sid, _ = obsidian_sync.story_ref(ent)
            if not sid or sid in seen or sid not in grouped:
                continue
            seen.add(sid)
            base = "stories/%s" % obsidian_sync.story_slug(sid)
            if owner:
                base = "%s/%s" % (owner, base)
            out.append((base, "Story %s" % sid, None))
        return out
    except Exception:
        return []


def _with_story_hubs(pairs, task, owner=None):
    """Append the grouped-story `## Related` pairs to an already-built related list.
    Shared by the vault mirror and the generic export so both carry the SAME story links
    (orthogonal to, and after, the category link). Returns the (possibly extended) list."""
    extra = _story_hub_pairs(task, owner)
    return (list(pairs) + extra) if extra else pairs


def _sync_obsidian_category_hubs(vault, owner):
    """Regenerate (toggle on) or prune (toggle off) the vault's category-hub pages
    under `<plugin>/<owner>/categories/`, from the sidecar index — like index.md.
    Best-effort + fully guarded: it must never break the mutation/export that triggered
    it, nor loop back into an export."""
    try:
        import export as _export, obsidian_sync
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        if os.path.isdir(pdir):
            _export.sync_category_hubs(pdir, enabled=_category_hubs_on(),
                                       subgroups=_subgroups_on())
    except Exception:
        pass


def _sync_obsidian_story_hubs(vault, owner):
    """Regenerate (toggle on) or prune (toggle off) the vault's story-hub pages under
    `<plugin>/<owner>/stories/`, from the on-disk notes — like the category hubs. The
    orthogonal, cross-category axis. Best-effort + fully guarded: it must never break
    the mutation/export that triggered it, nor loop back into an export."""
    try:
        import export as _export, obsidian_sync
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        if os.path.isdir(pdir):
            _export.sync_story_hubs(pdir, enabled=_story_groups_on())
    except Exception:
        pass


def _obsidian_related_links(task):
    """The render_note `related` list for a task's VAULT note — universal task↔task
    edges (lineage + semantic `touches-same`) ALWAYS, cited-note knowledge edges only
    when the knowledge-graph gate is on. Task stems resolve against the vault's
    sidecar index (via obsidian_sync.note_stem) so `## Related` links never dangle.
    Fully guarded — any failure degrades to []."""
    try:
        import obsidian_sync
        vault = _obsidian_vault()
        pdir = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), _owner()) if vault else None
        scan = all_tasks()
        by_id = {t.get("id"): t for t in scan}
        def stem_of(tid):
            other = by_id.get(tid)
            return obsidian_sync.note_stem(other, pdir) if other else None
        pairs = _related_pairs(task, scan, _knowledge_gate_on(), stem_of)
        return _with_story_hubs(_with_category_hub(pairs, task, _owner()), task, _owner())
    except Exception:
        return []


def _obsidian_sync(task):
    """Mirror a single task's note into the vault (create/update/done/save). No-op
    when export is off. Returns the note filename on success (used by the daily-note
    path), else None.

    Sandbox-safety net (mirrors the digest_dirty design): while a vault IS
    configured, a FAILED export marks the task obsidian_dirty and persists it, so
    the silent vault-drift becomes a recorded, re-syncable state; a SUCCESSFUL
    export clears that flag (and the persistent-failure signal).

    The mid-turn failure is SILENT on purpose: most mutations run in a SANDBOXED
    session that can't write a protected-root vault, but the unsandboxed Stop /
    SessionStart hook auto-flush (Fix B) heals it seconds later — so nagging every
    turn would be noise. The loud, actionable hint fires ONLY when a task is STILL
    dirty AFTER a hook flush also failed (obsidian_flush_failed) — a genuine
    persistent failure (vault gone, or even the unsandboxed hook is denied). The
    bookkeeping is fully guarded — it must never break the mutation that triggered
    the export, nor loop back into export (it writes via a direct save_task, which
    does NOT re-trigger the hook)."""
    vault = _obsidian_vault()
    if not vault:
        return None   # export OFF — never mark dirty (nothing to resync into)
    fname = None
    try:
        import obsidian_sync
        usage, prompts = _obsidian_note_data(task)
        related = _obsidian_related_links(task)
        fname = obsidian_sync.export_task(task, vault, usage=usage, prompts=prompts,
                                          related=related, owner=_owner())
    except Exception:
        # SILENT on purpose — no stderr line. A sandboxed hot-path denial is the common
        # case and self-heals via the unsandboxed hook flush; the dirty flag (surfaced by
        # `obsidian --status`) is the trace, and the loud remedy hint is reserved for a
        # confirmed PERSISTENT failure (below). Per-mutation noise would defeat Fix B.
        pass
    try:
        if fname:
            changed = clear_obsidian_dirty(task)
            if task.pop("obsidian_flush_failed", None) is not None:
                changed = True
            if changed:
                save_task(task)
            _clear_obsidian_perm_marker()   # things wrote again — re-arm the one-shot hint
            # The note wrote cleanly, so its category hub can be refreshed in place
            # (regenerated from the sidecar; toggle-off prunes it). Guarded no-op if the
            # toggle is off and there's nothing to prune.
            _sync_obsidian_category_hubs(vault, _owner())
            _sync_obsidian_story_hubs(vault, _owner())
        else:
            if mark_obsidian_dirty(task):
                save_task(task)
            # Persistent: a prior unsandboxed hook flush ALSO couldn't write this task.
            if task.get("obsidian_flush_failed"):
                _warn_obsidian_persistent_once(vault)
    except Exception:
        pass   # dirty/hint bookkeeping must never break the mutation
    return fname


def _obsidian_event(task, event):
    """Export the task AND (when the daily-note setting is on) append a dated line
    to the vault's daily note. `event` is "closed" or "checkpoint". Fully guarded."""
    fname = _obsidian_sync(task)
    if not fname:
        return
    try:
        import config, obsidian_sync
        if config.obsidian_daily_note_enabled():
            link = fname[:-3] if fname.endswith(".md") else fname
            obsidian_sync.append_daily_note(
                config.obsidian_vault(), link, event, task.get("title", ""),
                config.obsidian_daily_heading(), owner=_owner())
    except Exception as e:
        sys.stderr.write("task-station: Obsidian daily-note append failed: %s\n" % e)


# ---- Tasktrail event ledger (A-2) -------------------------------------------
# Durable, append-only JSONL record of every mutation (lib/stream.py). The in-blob
# event feed is a bounded rolling window (EVENTS_KEEP); the stream is the durable
# record + the substrate for the published contract. Wiring posture mirrors the
# obsidian hooks: a stream failure must NEVER block the mutation that triggered it.

def _stream_alloc_n(task, persist):
    """Allocate this task's next monotonic counter `n`. When `persist` (the normal
    path), bump the value ON THE TASK via the optimistic-rev lock so concurrent
    emitters stay gapless, and reflect it on the in-hand dict. When not persisting
    (a task.deleted tombstone — the row is gone), bump the in-hand dict only."""
    if persist and task.get("id"):
        box = {"n": None}
        def _bump(t):
            t["stream_n"] = int(t.get("stream_n", 0)) + 1
            box["n"] = t["stream_n"]
        if mutate(task["id"], _bump) is not None:
            task["stream_n"] = box["n"]
            return box["n"]
    task["stream_n"] = int(task.get("stream_n", 0)) + 1
    return task["stream_n"]


def _stream_emit(kind, task, data, session=None, persist=True):
    """Emit one Tasktrail event for `task`. Best-effort + fully guarded: a stream
    problem (config, disk, lock) is swallowed so it can never block the mutation
    that triggered it. No-op when the stream is disabled."""
    try:
        import config
        if not config.stream_enabled():
            return None
        import stream
        return stream.emit(kind, task, data,
                           lambda: _stream_alloc_n(task, persist),
                           actor_session=session, owner=_owner())
    except Exception:
        return None


def _stream_digest(task):
    """The machine-readable digest snapshot carried by task.checkpoint / task.snapshot.
    Model-curated digest fields only (never prompt text). `glossary` and `brief_path`
    are first-class in the contract but don't exist on the task yet — carry them
    generically WHEN PRESENT so the contract is forward-compatible."""
    d = {
        "title": (task.get("title") or "")[:EVENT_TEXT_MAX],
        "status": task_status(task),
        "goal": task.get("goal") or "",
        "state": task.get("state") or "",
        # ACTIVE steps only — a superseded step must no more ride a resume snapshot than
        # a superseded decision does. The elements themselves are carried unchanged, so
        # a task with nothing superseded emits byte-identically to before.
        "steps": [s for _i, s in _steps.live(task.get("steps"))],
        "summary": task.get("summary") or "",
        # Still-CURRENT decisions only, as plain strings: a superseded decision must
        # never ride a resume snapshot (that is the bug), and the plain-string
        # projection keeps the checkpoint contract byte-identical for older readers.
        "decisions": _dec.live_texts(task.get("decisions")),
        "prs": task.get("prs") or [],
        "stories": task.get("stories") or [],
    }
    if task.get("closed_ts"):
        d["closed"] = task["closed_ts"]
    for k in ("glossary", "brief_path"):
        if task.get(k) is not None:
            d[k] = task.get(k)
    return d


def _stream_updated_data(task, changed):
    """Payload for task.updated: the changed field NAMES plus the new values of the
    scalar fields, each capped at EVENT_TEXT_MAX (privacy — never a full body)."""
    fields = {}
    if "title" in changed:
        fields["title"] = (task.get("title") or "")[:EVENT_TEXT_MAX]
    # `summary↺` is --restore-summary: a different verb, the same field changing, so the
    # feed must carry the new value for it too.
    if {"summary", "summary+", "summary↺"} & set(changed):
        fields["summary"] = (task.get("summary") or "")[:EVENT_TEXT_MAX]
    if "state" in changed:
        fields["state"] = (task.get("state") or "")[:EVENT_TEXT_MAX]
    if "goal" in changed:
        fields["goal"] = (task.get("goal") or "")[:EVENT_TEXT_MAX]
    if "effort" in changed:
        fields["effort"] = task.get("effort")
    if "color" in changed:
        fields["color"] = task.get("color")
    return {"changed": list(changed), "fields": fields}


def _stream_created_data(task):
    d = {"title": (task.get("title") or "")[:EVENT_TEXT_MAX],
         "status": task.get("status")}
    for k in ("color", "effort", "goal"):
        if task.get(k) is not None:
            d[k] = task.get(k)
    return d


def sorted_tasks():
    """Not-closed (open + active) before closed; within each, most recent
    activity first."""
    return sorted(
        all_tasks(),
        key=lambda t: (1 if is_closed(t) else 0, -t.get("updated_ts", 0)),
    )


def _max_seq(tasks=None):
    tasks = tasks if tasks is not None else all_tasks()
    return max((t.get("seq") or 0 for t in tasks), default=0)


def ensure_seqs():
    """Backfill stable per-task sequence numbers, assigned in creation order.

    Every task gets a permanent `seq` the first time it's seen — the number a
    user sees in `/todo` and types as `/todo <n>`. Unlike the old render-time
    index, a task keeps its number even as others are added, closed, or reorder
    by recent activity. Idempotent: tasks that already have a seq keep it.
    """
    tasks = all_tasks()
    missing = [t for t in tasks if not t.get("seq")]
    if not missing:
        return
    n = _max_seq(tasks)
    for t in sorted(missing, key=lambda t: t.get("created_ts", 0)):
        n += 1
        t["seq"] = n
        save_task(t)


# ------------------------------------------------------------------ links ----

def get_link(session):
    return _backend().get_link(session)


def set_link(session, task_id):
    _backend().set_link(session, task_id)


def clear_link(session):
    _backend().clear_link(session)


def live_session_count(task):
    """How many of this task's recorded sessions are STILL attached to it.

    `task["sessions"]` is append-only — it keeps every session that ever touched
    the task, even ones that later attached elsewhere, closed, or were skipped —
    so a raw `len()` over-reports. The live count is the sessions whose link
    currently resolves back to this task; that's the real concurrent-session
    signal /todo surfaces."""
    return _backend().live_session_count(task)


def get_count(session):
    """How many prompts this session has gone without attaching to a task."""
    return _backend().get_count(session)


def bump_count(session):
    return _backend().bump_count(session)


def clear_count(session):
    _backend().clear_count(session)


# -- edited / blocked markers: the "real work happened" enforcement signal -----
# A session that has EDITED a file but has no attached task is doing untracked
# work. `.edited` records that an edit happened; `.blocked` counts how many times
# the Stop gate has refused to let the turn end, so a non-complying loop can't
# wedge the session (we give up after STOP_GATE_MAX_BLOCKS).
STOP_GATE_MAX_BLOCKS = 2


def mark_edited(session):
    """Record that this session edited a file. Returns True only on the FIRST
    call (so the PostToolUse reminder is one-shot, not per-edit)."""
    return _backend().mark_edited(session)


def has_edited(session):
    return _backend().has_edited(session)


def get_blocked(session):
    return _backend().get_blocked(session)


def bump_blocked(session):
    return _backend().bump_blocked(session)


def clear_edit_markers(session):
    _backend().clear_edit_markers(session)


# -------------------------------------------------------------- utilities ----

def rel_time(ts):
    if not ts:
        return "—"
    d = max(0, int(_now() - ts))
    if d < 60:
        return "just now"
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    if d < 7 * 86400:
        return "%dd ago" % (d // 86400)
    return datetime.fromtimestamp(ts).strftime("%b %-d")


_ORDINAL_REF_RE = re.compile(r"^(\d+)-(\d+)$")


def resolve_ref(ref):
    """Resolve a /todo argument to a task dict.

    An all-digit ref is matched against tasks' stable `seq` numbers (the numbers
    shown in the listing). A `<seq>-<ordinal>` ref (`4-0`) names one specific HUB
    SESSION of task 4 and resolves to that TASK here — the ordinal is consumed by
    the jump path (see `_parse_ordinal_ref`), not by task resolution. Anything else
    — or a digit / `<seq>-<n>` string matching no seq — is matched against task ids
    by exact match or prefix, so a longer all-digit id prefix that happens to
    contain no hex letters (e.g. "03471986") still resolves correctly.

    ORDER is what keeps the session grammar from shadowing an id. A task id is a
    uuid4 STRING, so it does contain hyphens — the first always at index 8. The only
    ref that could be read both ways is therefore an 8-digit all-numeric first block
    plus an all-digit partial second block ("03471986-1234"). The `<seq>-<ordinal>`
    branch claims such a ref ONLY when a task really carries that seq (seqs are
    small; a 7-8 digit one does not occur), and otherwise falls through to the
    id-prefix branch with its behaviour unchanged.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    ensure_seqs()
    listing = sorted_tasks()
    if ref.isdigit():
        i = int(ref)
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that number: fall through and treat as an id prefix.
    m = _ORDINAL_REF_RE.match(ref)
    if m:
        i = int(m.group(1))
        for t in listing:
            if t.get("seq") == i:
                return t
        # No task with that seq: fall through and treat the whole ref as an id prefix.
    for t in listing:
        if t["id"] == ref or t["id"].startswith(ref):
            return t
    return None


def _parse_ordinal_ref(ref):
    """`(task, ordinal)` when `ref` is a `<seq>-<ordinal>` session handle naming a
    REAL task, else None.

    The ordinal comes back as an int and **0 is a perfectly valid ordinal** (it is
    the session that created the task), so every caller must test `is None` — never
    truthiness.

    Requiring the task to EXIST is what makes this safe to consult before id-prefix
    resolution: a ref that merely looks like the grammar but names no such seq (say
    the uuid prefix "03471986-1234") returns None, and the caller falls through to
    ordinary id resolution with its behaviour unchanged."""
    m = _ORDINAL_REF_RE.match((ref or "").strip())
    if not m:
        return None
    ensure_seqs()
    seq = int(m.group(1))
    for t in sorted_tasks():
        if t.get("seq") == seq:
            return t, int(m.group(2))
    return None


def _resolve_prompt_task_refs(prompt, limit=3):
    """Detect task references in the user's prompt and resolve them to real
    tasks, so the agent is handed the seq->id->title binding instead of guessing.
    Returns a printable block (str), or "" when nothing resolves."""
    if not prompt:
        return ""
    refs = []
    # "task 387", "task #387", "todo 387", "seq 387", "#387"
    for m in re.finditer(r"(?i)(?:\b(?:task|todo|seq)\s+#?|#)(\d{1,7})\b", prompt):
        refs.append(m.group(1))
    # bracketed hex short-id "[6e756ed6]"
    for m in re.finditer(r"\[([0-9a-f]{6,40})\]", prompt):
        refs.append(m.group(1))
    seen, out = set(), []
    for r in refs:
        t = resolve_ref(r)
        if not t or t["id"] in seen:
            continue
        seen.add(t["id"])
        seq = t.get("seq") or "?"
        status = t.get("status") or "open"
        out.append('  - #%s [%s] "%s" - %s - digest: task-station search --detail %s'
                   % (seq, t["id"][:8], t.get("title") or "", status, seq))
        if len(out) >= limit:
            break
    if not out:
        return ""
    return "[task-station] Your message references an existing task - resolved:\n" + "\n".join(out)


_DEDUP_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "all", "new", "add", "fix",
    "update", "make", "use", "via", "per", "out", "off", "this", "that",
}


def _norm_tokens(s):
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {t for t in toks if len(t) > 2 and t not in _DEDUP_STOPWORDS}


def _norm_nums(s):
    """Numeric identifiers in a title (PR/bug/story numbers, phase numbers)."""
    return set(re.findall(r"\d+", s or ""))


# --- F9 identity-keyed fold-in -------------------------------------------------
# Strong identity keys let the attach/nudge path join tasks on IDENTITY (the PR or
# work-item the prompt names) instead of FLAVOR (shared process words). This is the
# same principle create-dedup already applies via _norm_nums (see similar_open_task),
# lifted to a typed, shared extractor so a PR number can never join a story number.
# A key is a typed string: "pr:<n>" | "wi:<n>". Fail-open — bad/None input → set().

_PR_WORDS = {"pr", "pull", "pullrequest", "pullrequests"}


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


def add_ledger(task, action, worker_sid=None, actor_sid=None, detail=None):
    """Append one hub<->worker interaction to the task's provenance ledger:
    {ts, action, worker, actor, actor_ordinal, detail}. UNBOUNDED append-only
    (task #463 design decision: complete provenance is the point — NO trim; if
    blob growth ever bites, revisit with a separate ledger store, not a silent
    cap). `actor_ordinal` is resolved NOW (ordinals are stable/never reused, so
    freezing it at write time is safe and keeps reads cheap). Does NOT save — the
    caller persists."""
    e = {"ts": _now(), "action": action, "worker": worker_sid,
         "actor": actor_sid,
         "actor_ordinal": hub_ordinal(task, actor_sid) if actor_sid else None,
         "detail": (detail or "")[:EVENT_TEXT_MAX] or None}
    task.setdefault("ledger", []).append(e)
    return e


# -- memo correspondence -------------------------------------------------------
# A memo hands a fact/decision to a task's working session(s) without copy-paste.
# The record lives in task["memos"]; a capped preview rides the event feed (its id
# SHARED with the memo). Each session explicitly acks on a shared, visible ledger so
# multiple sessions on one task never double-implement. A memo is NOT a decision;
# acking MAY optionally promote it to one.

def _memo_src_label(from_sid, from_task):
    """Compact 'who sent it' tag for a memo preview: '9e01f2·#41' | '9e01f2' | 'desktop'.
    `from_sid` is a session id or a free-text Desktop/MCP signature (display [:6]);
    `from_task` is the sender's attached task id, rendered as its #seq when it loads."""
    who = (from_sid[:6] if from_sid else "desktop")
    if from_task:
        ft = load_task(from_task)
        tag = "#%s" % (ft.get("seq", from_task[:6]) if ft else from_task[:6])
        return "%s·%s" % (who, tag)
    return who


def _memo_pending_for(memo, session):
    """A memo is pending for session S iff S is set, S didn't send it, and S hasn't
    acked it (ack-gated; ignores any seen_ts high-water mark)."""
    if not session or session == memo.get("from_sid"):
        return False
    return session not in {a.get("sid") for a in (memo.get("acks") or [])}


def memo_pending(task, session):
    """Memos on `task` still awaiting `session`'s ack, oldest-first. Empty for a task
    with no memos feed (back-compat) or for the sender's own session."""
    return [m for m in (task.get("memos") or []) if _memo_pending_for(m, session)]


def _trim_memos(task):
    """Bound task["memos"]. Beyond MEMOS_KEEP, drop the oldest memos that are pending
    for NO session registered on the task (an unacked memo is otherwise never lost).
    Past MEMOS_HARD_CAP, drop oldest unconditionally. Does NOT save."""
    memos = task.get("memos") or []
    sessions = task.get("sessions") or []

    def fully_acked(m):
        if not sessions:            # nobody registered to ack yet — never soft-trim
            return False
        return not any(_memo_pending_for(m, s) for s in sessions)

    i = 0
    while len(memos) > MEMOS_KEEP and i < len(memos):
        if fully_acked(memos[i]):
            del memos[i]
        else:
            i += 1
    if len(memos) > MEMOS_HARD_CAP:
        del memos[: len(memos) - MEMOS_HARD_CAP]


def memo_corrections(memo):
    """The `--corrects` targets a memo declares, as a list of strings. Empty for an
    ordinary memo and for every memo written before the field existed (back-compat)."""
    if not isinstance(memo, dict):
        return []
    return [str(c) for c in (memo.get("corrects") or []) if str(c).strip()]


def memo_send(task, text, from_sid=None, corrects=None):
    """Post correspondence onto `task`. Creates task["memos"] on first send. Records a
    memo dict + a capped 'memo' preview event whose id is SHARED with the memo, then
    trims. Returns the memo dict. Does NOT save — the caller persists.

    `corrects` declares what this memo REPLACES — a memory-note slug, `decision:<n>` on
    the target task, or another memo's id8. A memo that declares corrections cannot be
    acked without a disposition that engages them (see memo_ack_disposition): the
    incident behind this was a correction acked as a receipt and never integrated, so
    the durable layer kept saying the opposite. Omitted entirely when empty, so an
    ordinary memo's record is shaped exactly as before."""
    text = (text or "").strip()
    from_task = (get_link(from_sid) if from_sid else None)
    memo = {"id": None, "ts": _now(), "from_sid": from_sid, "from_task": from_task,
            "text": text, "acks": []}
    targets = [str(c).strip() for c in (corrects or []) if str(c).strip()]
    if targets:
        memo["corrects"] = targets
    # The preview event's id IS the memo id — one identity across the feed + the record.
    ev = add_event(task, "memo", "", from_sid)
    memo["id"] = ev["id"]
    src = _memo_src_label(from_sid, from_task)
    preview = " ".join(memo["text"].split())
    ev["text"] = ("memo %s from %s: %s" % (ev["id"][:8], src, preview))[:EVENT_TEXT_MAX]
    task.setdefault("memos", []).append(memo)
    _trim_memos(task)
    return memo


# The three ways an ack may DISPOSE of a memo. A bare ack is no longer one of them:
# an ack is a receipt, and a receipt was mistaken for an integration once already.
MEMO_DISPOSITIONS = ("decision", "memory", "noop")
MEMO_DISPOSITION_HELP = (
    "an ack must say what it DID with the memo — pass exactly one of:\n"
    "  --decision [TEXT]   promote it to a decision on this task\n"
    "  --memory <slug>     record that it was folded into that agent-memory note\n"
    "  --noop \"<reason>\"   no durable change needed (the reason is mandatory)")


def memo_ack_disposition(decision=None, memory=None, noop=None):
    """Resolve the ack disposition from the three mutually-exclusive flags.

    Returns `(disposition, error)` where disposition is `{"kind","value"}` and exactly
    one of the two is None. This is the M1 gate: a BARE ack — the shape that let a
    correction be acknowledged without ever reaching the durable layer — is now an
    error naming all three options. `--noop` is a legitimate answer, but it must be
    said out loud and with a reason, which is the whole point: it removes the
    incentive to batch-ack, since each memo now needs its own disposition."""
    given = [k for k, v in (("decision", decision is not None),
                            ("memory", memory is not None),
                            ("noop", noop is not None)) if v]
    if not given:
        return None, "memo ack: " + MEMO_DISPOSITION_HELP
    if len(given) > 1:
        return None, ("memo ack: pass exactly ONE disposition, got %s — an ack records "
                      "one thing done with the memo." % ", ".join("--" + g for g in given))
    kind = given[0]
    if kind == "memory":
        slug = str(memory).strip()
        if not slug:
            return None, "memo ack: --memory needs the slug of the note it was folded into"
        return {"kind": "memory", "value": slug}, None
    if kind == "noop":
        reason = str(noop).strip()
        if not reason:
            return None, ("memo ack: --noop requires a reason — say WHY no durable change "
                          "is needed, or use --decision/--memory instead")
        return {"kind": "noop", "value": reason}, None
    # --decision may be a bare flag (const True → promote the memo body verbatim) or TEXT.
    return {"kind": "decision",
            "value": (decision if isinstance(decision, str) else "")}, None


def memo_ack(task, memo, session, promote=False, decision_text=None, disposition=None):
    """Record `session`'s ack on the memo's shared ledger. Idempotent — a duplicate sid
    returns "already" with no second entry and no second event. A fresh ack appends
    {sid, ts} and posts a 'memo-ack' event. `promote` (from `--decision [TEXT]`)
    promotes the memo to a decision on a fresh ack, using `decision_text` or the memo
    body, attributed to the acking session. Returns "acked" | "already". No save.

    `disposition` ({"kind","value"} from memo_ack_disposition) is recorded ON the ack
    entry, so the ledger shows not just WHO acked but what each of them DID with it.
    Enforcement of "exactly one disposition" lives at the CLI boundary (cmd_memo), not
    here: this stays a permissive mutator so the MCP/Desktop ack surface and internal
    callers keep working."""
    acks = memo.setdefault("acks", [])
    if any(a.get("sid") == session for a in acks):
        return "already"
    entry = {"sid": session, "ts": _now()}
    if disposition:
        entry["disposition"] = dict(disposition)
    acks.append(entry)
    add_event(task, "memo-ack",
              "memo %s acked by %s%s" % ((memo.get("id") or "")[:8], (session or "?")[:8],
                                         (" (%s)" % disposition["kind"]) if disposition else ""),
              session)
    if promote:
        append_decision(task, (decision_text or "").strip() or memo.get("text", ""), session)
    return "acked"


def _memo_by_prefix(task, prefix):
    """Resolve an id-prefix to a single memo on `task`. Returns (memo, None) on a unique
    match; (None, error-line) when the id is missing, matches nothing, or is ambiguous
    (the error lists the candidate id8s)."""
    prefix = (prefix or "").strip()
    if not prefix:
        return None, "memo: an --id (or id prefix) is required"
    hits = [m for m in (task.get("memos") or []) if (m.get("id") or "").startswith(prefix)]
    if not hits:
        return None, "memo: no memo matching id %r on this task" % prefix
    if len(hits) > 1:
        return None, ("memo: id %r is ambiguous — candidates: %s"
                      % (prefix, ", ".join(m["id"][:8] for m in hits)))
    return hits[0], None


# -- delta-injection: per-session "while you were away" brief ------------------
# The events feed (task["events"], authored by the sibling core-model workstream)
# is the single delta source; each session_meta entry carries a `seen_ts`
# high-water mark. `delta_brief` reports OTHER sessions' events newer than that
# mark; `mark_seen` advances it. Both degrade to no-op on a task with no events
# feed (the field is simply absent), so bare/legacy tasks inject nothing.

def mark_seen(task, session):
    """Advance this session's delta high-water mark to now so already-surfaced
    events never re-inject. Creates a minimal session_meta entry for an unknown
    sid rather than KeyError-ing. Does NOT save — the caller persists."""
    if not session:
        return
    task.setdefault("session_meta", {}).setdefault(session, {})["seen_ts"] = _now()


def delta_brief(task, session, max_items=DELTA_MAX_ITEMS, max_chars=DELTA_MAX_CHARS):
    """One bounded "while you were away" block, or None when there's nothing to
    say. Selects `events` newer than this session's `seen_ts` (default: the
    session's `session_meta` attach `ts`, else 0) and NOT authored by this
    session (a missing/None `sid` counts as "other"). Rendered newest-last,
    capped to `max_items` newest events and `max_chars` total. Absent/empty
    events → None, so bare tasks stay silent."""
    events = task.get("events") or []
    if not events:
        return None
    meta = (task.get("session_meta") or {}).get(session) or {}
    seen = meta.get("seen_ts")
    if seen is None:
        seen = meta.get("ts") or 0
    # `memo` arrivals are owned by memo_pending_brief (ack-gated, re-surfaces until
    # acked); excluding them here avoids a double-announce. `memo-ack` still flows,
    # so a session sees when a peer acks.
    fresh = [ev for ev in events
             if (ev.get("ts") or 0) > seen and ev.get("sid") != session
             and ev.get("kind") != "memo"]
    if not fresh:
        return None
    fresh.sort(key=lambda e: e.get("ts") or 0)      # oldest → newest (newest-last)
    total = len(fresh)
    seq = task.get("seq", task["id"][:8])
    footer = "(full trail: /todo %s history)" % seq

    def _line(ev):
        text = (ev.get("text") or "").strip()
        who = session_display(task, ev.get("sid"))          # hub '<seq>-<n>' / worker slug (#463)
        who_tag = ("[%s] " % who) if who and who != "?" else ""
        return "  • %s (%s) %s%s" % (rel_time(ev.get("ts")), ev.get("kind", "?"),
                                     who_tag, text)

    shown = fresh[-max_items:]
    while True:
        head = ("[task-station] While you were away — %d update(s) to #%s [%s] "
                "by other sessions:" % (total, seq, task["id"][:8]))
        body = [head] + [_line(ev) for ev in shown]
        earlier = total - len(shown)
        if earlier > 0:
            body.append("  (+%d earlier)" % earlier)
        body.append(footer)
        text = "\n".join(body)
        if len(text) <= max_chars or len(shown) <= 1:
            return text
        shown = shown[1:]     # over budget: drop the OLDEST shown line, keep newest


def memo_pending_brief(task, session):
    """One bounded "memo(s) awaiting YOUR ack" block for `session`, or None when none
    are pending. UNLIKE delta_brief this is ack-gated, NOT seen_ts-gated — a pending
    memo re-surfaces on every injection until this session explicitly acks it, so a
    fact handed to a task is never silently missed. Renders newest-last, ≤
    MEMO_PENDING_MAX lines (≤ MEMO_LINE_MAX chars each), each with the shared ack
    ledger inline so peers can see who's already handled it (no double-implement).
    None on a task with no memos feed (back-compat)."""
    pending = memo_pending(task, session)                # oldest-first, ack-gated
    if not pending:
        return None
    shown = pending[-MEMO_PENDING_MAX:]                  # newest-last within the cap
    out = ["[task-station] %d memo(s) awaiting YOUR ack on [%s]:"
           % (len(pending), task["id"][:8])]
    for m in shown:
        body = " ".join((m.get("text") or "").split())
        ackers = [a.get("sid", "")[:8] for a in (m.get("acks") or []) if a.get("sid")]
        ledger = ", ".join(ackers) if ackers else "(none yet)"
        src = _memo_src_label(m.get("from_sid"), m.get("from_task"))
        # Reserve room for the ledger — truncate the BODY, never the whole line, so the
        # "acked by" signal (peers' acks → no double-implement) always survives.
        # A memo that declares corrections (or merely READS like one) is tagged right
        # here, so its weight is visible before it is opened — the nag is often the only
        # surface a fresh session sees.
        corrects = memo_corrections(m)
        if corrects:
            tag = " [CORRECTS: %s]" % ", ".join(corrects)
        elif correction_language(m.get("text")):
            tag = " [reads as a correction]"
        else:
            tag = ""
        prefix = '  • %s (%s, from %s)%s ' % (m["id"][:8], rel_time(m.get("ts")), src, tag)
        suffix = ' — acked by: %s' % ledger
        budget = MEMO_LINE_MAX - len(prefix) - len(suffix) - 2   # 2 = the wrapping quotes
        if budget < 12:                                          # pathological: keep it sane
            budget = 12
        if len(body) > budget:
            body = body[:budget - 1].rstrip() + "…"
        out.append('%s"%s"%s' % (prefix, body, suffix))
    extra = len(pending) - len(shown)
    if extra > 0:
        out.append("  (+%d more pending)" % extra)
    out.append("Read full body:  /todo memo show <id8>")
    # An ack must now say what it DID with the memo — a bare ack is rejected. That is the
    # whole fix: an ack is a receipt, and a receipt was once mistaken for an integration.
    out.append("Acknowledge:     /todo memo ack <id8> <decision text>   (auto-signs with "
               "this session)")
    out.append("  … or          /todo memo ack <id8> memory:<note-slug>   (folded into "
               "that memory note)")
    out.append("  … or          /todo memo ack <id8> noop:<reason>       (no durable "
               "change needed)")
    return "\n".join(out)


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


RUNS_CAP = 50   # per-run records kept on a task (most-recent), so a long-lived task can't grow unbounded


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
    usage.PROJECTS_ROOT = PROJECTS_ROOT
    usage.WORKERS_REGISTRY = DELEGATE_REGISTRY
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


# --------------------------------------------------- WS4 board usage view-model ---
#
# The board's per-task usage panels (model-mix bar, per-session table, work-mix phase
# bar, derivation footnote, prompts preview) read a flattened view-model built here so
# tools/render_board.py stays a pure renderer. Everything is defensive: usage tracking
# off, an empty ledger, or ANY error degrades the block to absent — a board must render.

def _family_mix(bucket_by_model):
    """[{'family','pct','unpriced'}] merged by model family from a
    {model_id: {'out', 'cost_usd'(None=unpriced), ...}} dict, sorted by share desc.
    Share is by derived-cost when every model is priced, else by output-token count
    (so an unknown-model task still shows a proportional bar)."""
    import pricing
    fams = {}
    total_cost = 0.0
    total_out = 0
    any_unpriced = False
    for mid, d in (bucket_by_model or {}).items():
        fam = pricing.model_family(mid)
        f = fams.setdefault(fam, {"family": fam, "cost": 0.0, "out": 0, "unpriced": False})
        c = d.get("cost_usd")
        if c is None:
            f["unpriced"] = True
            any_unpriced = True
        else:
            f["cost"] += c or 0.0
            total_cost += c or 0.0
        o = d.get("out") or 0
        f["out"] += o
        total_out += o
    segs = []
    for f in fams.values():
        if not any_unpriced and total_cost > 0:
            pct = f["cost"] / total_cost
        elif total_out:
            pct = f["out"] / total_out
        else:
            pct = 0.0
        segs.append({"family": f["family"], "pct": pct, "unpriced": f["unpriced"]})
    segs.sort(key=lambda s: (-s["pct"], s["family"]))
    return segs


def _merge_row_models(row):
    """A session_usage row's parent `models` + subagent `sidechain` blobs merged into
    one {model_id: {'in','out','cache_read','cost_usd'(None when any part unpriced)}}
    dict — the input to _family_mix for that session's own model mini-mix."""
    agg = {}
    for blob in (row.get("models"), row.get("sidechain")):
        for mid, d in (blob or {}).items():
            w = agg.setdefault(mid, {"in": 0, "out": 0, "cache_read": 0,
                                     "cost_usd": 0.0, "_unpriced": False})
            w["in"] += d.get("in") or 0
            w["out"] += d.get("out") or 0
            w["cache_read"] += d.get("cache_read") or 0
            c = d.get("cost_usd")
            if c is None:
                w["_unpriced"] = True
            else:
                w["cost_usd"] += c or 0.0
    for w in agg.values():
        if w.pop("_unpriced"):
            w["cost_usd"] = None
    return agg


def _phase_weight(v):
    """A single phase blob value → a non-negative numeric weight for the work-mix bar.
    WS3 owns the exact shape (built in parallel); this reads a bare number or the first
    numeric among a handful of likely keys, so the bar lights up whatever WS3 stores."""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return max(0.0, float(v))
    if isinstance(v, dict):
        for k in ("cost_usd", "weight", "out", "tokens", "msgs", "seconds", "pct"):
            x = v.get(k)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                return max(0.0, float(x))
    return 0.0


def _phase_segments(rows):
    """WS3 phase blobs merged across the task's session rows → [{'label','pct'}] sorted
    by share desc. Empty (block omitted) when WS3 hasn't populated any phases yet.

    Only real phase names (`phases.PHASES`) are counted: the stored blob carries a
    `__v` version stamp (and could carry other non-phase keys), which must never leak
    out as a `__v` bar segment — same whitelist usage.py applies when aggregating."""
    import phases
    totals = {}
    names = {}                          # "other" drill-down: tool/command → count
    for r in rows:
        for label, v in (r.get("phases") or {}).items():
            if label not in phases.PHASES:
                continue
            totals[label] = totals.get(label, 0.0) + _phase_weight(v)
            if label == "other" and isinstance(v, dict):
                for sig, n in (v.get("names") or {}).items():
                    names[sig] = names.get(sig, 0) + (n or 0)
    grand = sum(totals.values())
    if grand <= 0:
        return []
    segs = [{"label": k, "pct": v / grand} for k, v in totals.items() if v > 0]
    segs.sort(key=lambda s: (-s["pct"], s["label"]))
    # attach the top 'other' contributors (desc by count) for the board drill-down.
    if names:
        top = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        for s in segs:
            if s["label"] == "other":
                s["names"] = [{"name": n, "count": c} for n, c in top]
    return segs


def _usage_rate_rows(models):
    """The published $/MTok rate rows ACTUALLY used by this task — one per distinct
    priced family present (unknown/unpriced models carry no sheet → skipped), for the
    board's derivation table. Keyed off the real model ids so the family/version and
    date-dependent Sonnet pricing resolve exactly as the cost derivation did."""
    import pricing
    seen = set()
    out = []
    for mid in sorted(models):
        rate = pricing.rates_for(mid)
        if rate is None:
            continue
        fam = pricing.model_family(mid)
        if fam in seen:
            continue
        seen.add(fam)
        out.append({"family": fam, "model": mid, "in": rate["in"], "out": rate["out"],
                    "w5m": rate["w5m"], "w1h": rate["w1h"], "read": rate["read"]})
    return out


def _board_usage(task, live_sids=None):
    """Assemble the board usage view-model for one task: the task-level model mix, the
    per-session breakdown (per-session mini-mix, tokens, $ derived + $ reported from the
    delegate runs, live flag when WS5 supplies live_sids), token/$ totals with the
    reported cross-check, the phase (work-mix) segments, the rate rows used, and the
    derivation note. Returns None (block omitted) when usage tracking is off, the ledger
    is empty, or the store/data-shaping below hits a hiccup — never breaks a render. The
    two gate checks live OUTSIDE the try (WS7 debug lesson: a bare `except Exception`
    around the whole function once swallowed a real bug right alongside the expected
    off/empty cases, costing real debug time)."""
    import config as _cfg
    if not _cfg.usage_tracking_enabled():
        return None
    try:
        store = _backend()
        data = _usage_engine().task_usage(store, task)
    except Exception:
        return None
    models = data.get("models") or {}
    if not models:
        return None
    try:
        live = set(live_sids or [])
        rows = {r.get("session_id"): r for r in store.session_usage_for_task(task["id"])}
        # $ reported per session, summed from the delegate per-run records (WS2).
        reported_by_sid = {}
        for run in (task.get("runs") or []):
            sid = run.get("session_id")
            c = run.get("cost_usd")
            if sid and isinstance(c, (int, float)) and not isinstance(c, bool):
                reported_by_sid[sid] = reported_by_sid.get(sid, 0.0) + float(c)
        sessions = []
        for s in data.get("sessions") or []:
            sid_full = s.get("session_id")
            sessions.append({
                "sid": s.get("sid"),
                "role": s.get("role") or "unknown",
                "label": s.get("label"),
                "live": sid_full in live,
                "in": s.get("in") or 0,
                "out": s.get("out") or 0,
                "cache_read": s.get("cache_read") or 0,
                "cost_usd": s.get("cost_usd"),
                "reported": reported_by_sid.get(sid_full),
                "mix": _family_mix(_merge_row_models(rows.get(sid_full) or {})),
            })
        usage_vm = {
            "mix": _family_mix(models),
            "total_in": data.get("total_in", 0),
            "total_out": data.get("total_out", 0),
            "total_cost_usd": data.get("total_cost_usd", 0.0),
            "reported_cost_usd": data.get("reported_cost_usd", 0.0),
            "any_unpriced": bool(data.get("any_unpriced")),
            "derived_note": data.get("derived_note", ""),
            "rates": _usage_rate_rows(models),
        }
        return {"usage": usage_vm, "sessions": sessions,
                "phases": _phase_segments(list(rows.values()))}
    except Exception:
        return None


def _board_prompt_rows(task):
    """The task's enriched, chronological prompt trail (session-attributed) for the
    board's panels, or [] when usage tracking OR the `board_prompts` display gate is
    off (or nothing captured). Reuses usage.task_prompts so the board, the CLI, and
    the MCP tool share ONE attribution join. Display is local-only (the board never
    leaves the machine); prompt EXPORT stays opt-in elsewhere. Defensive — a store
    hiccup degrades to [] rather than breaking the board render."""
    try:
        import config as _cfg
        if not _cfg.board_prompts_enabled():
            return []
        return _usage_engine().task_prompts(_backend(), task)
    except Exception:
        return []


def _board_prompt_view(r):
    """One prompt row shaped for the board view-model (ts/kind/text + session
    attribution for the full-list panel)."""
    return {"ts": r.get("ts"), "kind": r.get("kind") or "prompt",
            "text": r.get("text") or "", "role": r.get("role"),
            "label": r.get("label"), "sid": r.get("sid")}


def _board_prompts(task, limit=5):
    """The last `limit` captured prompts (most-recent last) for the board's collapsed
    Recent-prompts preview. [] when the ledger / board_prompts gate is off."""
    return [_board_prompt_view(r) for r in _board_prompt_rows(task)[-limit:]]


def _board_prompts_all(task):
    """The FULL captured prompt trail (oldest first) for the board's expandable
    'All prompts' <details>. [] when the ledger / board_prompts gate is off."""
    return [_board_prompt_view(r) for r in _board_prompt_rows(task)]


def _prompt_is_human(kind, text):
    """True when a captured prompt reads as a genuine HUMAN-typed message (board B8),
    False for a Claude/slash/hook-generated one — a slash `command`, a compaction
    `compact` summary, or text that opens with a `/` slash-command, a `<command-…>`
    managed wrapper, or a `<!--` HTML comment. The last case catches the EXPANDED body
    of a slash command (Claude Code records `/todo`, `/save`, etc. as a user turn whose
    text is the command file's markdown, which the bare aliases lead with a
    `<!-- task-station-managed: … -->` marker) — the noise that used to bloat the trail.

    Also filtered as GENERATED (they arrive as user-role turns but no human typed them):
    any leading `<tag …>` harness/managed wrapper (`<command-…>`, `<task-notification>`,
    `<system-reminder>`, skill guards like `<SUBAGENT-STOP>`, …), a Skill-tool injection
    (`Base directory for this skill: …`), and the `[Request interrupted by user]`
    marker the harness records on an interrupt."""
    if kind in ("command", "compact"):
        return False
    t = (text or "").lstrip()
    if t.startswith("/") or t.startswith("<!--"):
        return False
    if re.match(r"<[A-Za-z][\w.-]*[\s>]", t):          # any tag-like managed wrapper
        return False
    if t.startswith("Base directory for this skill:"):
        return False
    if t.startswith("[Request interrupted"):
        return False
    return True


def _cost_band_thresholds(costs):
    """(lo, hi) = (μ, μ+σ) of the priced per-session derived $ once ≥3 priced sessions
    exist, else the fixed (0.01, 0.05) fallback — mirrors hud.py `_session_thresholds`
    so the board colours cost figures with the SAME stdev bands as the HUD costbar."""
    import math
    priced = [float(c) for c in costs
              if isinstance(c, (int, float)) and not isinstance(c, bool)]
    if len(priced) >= 3:
        n = len(priced)
        mean = sum(priced) / n
        var = sum((x - mean) ** 2 for x in priced) / n
        sd = math.sqrt(var) if var > 0 else 0.0
        return [round(mean, 6), round(mean + sd, 6)]
    return [0.01, 0.05]


def _session_usage_summary(row):
    """A per-session usage summary from ONE ledger row: merged model mix, token totals,
    derived $ (None when any model unpriced), msg count, and the per-session work-mix.
    `{}`-safe: an absent row degrades to a zeroed summary."""
    mm = _merge_row_models(row) if row else {}
    costs = [d.get("cost_usd") for d in mm.values()]
    any_unpriced = any(c is None for c in costs)
    return {
        "in": sum(d.get("in") or 0 for d in mm.values()),
        "out": sum(d.get("out") or 0 for d in mm.values()),
        "cache_read": sum(d.get("cache_read") or 0 for d in mm.values()),
        "cost_usd": None if any_unpriced else round(sum(c or 0.0 for c in costs), 6),
        "any_unpriced": any_unpriced,
        "msgs": sum(d.get("msgs") or 0 for d in mm.values()),
        "mix": _family_mix(mm),
        "phases": _phase_segments([row]) if row else [],
    }


def _aggregate_usage_summary(rows):
    """Merge several ledger rows into ONE usage summary (the hub-plus-its-workers
    aggregate): merged model mix, summed tokens, derived $ (None when ANY part is
    unpriced), and the merged work-mix across all rows."""
    merged = {}
    for row in rows:
        for mid, d in (_merge_row_models(row) or {}).items():
            w = merged.setdefault(mid, {"in": 0, "out": 0, "cache_read": 0,
                                        "cost_usd": 0.0, "_unpriced": False, "msgs": 0})
            w["in"] += d.get("in") or 0
            w["out"] += d.get("out") or 0
            w["cache_read"] += d.get("cache_read") or 0
            w["msgs"] += d.get("msgs") or 0
            c = d.get("cost_usd")
            if c is None:
                w["_unpriced"] = True
            else:
                w["cost_usd"] += c or 0.0
    for w in merged.values():
        if w.pop("_unpriced"):
            w["cost_usd"] = None
    costs = [d.get("cost_usd") for d in merged.values()]
    any_unpriced = any(c is None for c in costs)
    return {
        "in": sum(d.get("in") or 0 for d in merged.values()),
        "out": sum(d.get("out") or 0 for d in merged.values()),
        "cache_read": sum(d.get("cache_read") or 0 for d in merged.values()),
        "cost_usd": None if any_unpriced else round(sum(c or 0.0 for c in costs), 6),
        "any_unpriced": any_unpriced,
        "mix": _family_mix(merged),
        "phases": _phase_segments(rows),
    }


def _board_hub_cards(task, live_sids=None):
    """The per-hub view-model for the board's restructured expanded panel (board
    B10–B14): one card PER HUB session, each baking in its own prompts, its cost +
    work-mix, and its nested worker sessions (with per-worker cost + work-mix + prompts
    folded in). The `main` hub (the `/todo <n>` resume target) and the pinned hub are
    flagged so the renderer can float + highlight them.

    Returns `{"hubs": [...], "cost_thresholds": [lo, hi]}`. `[]`/fallback thresholds
    when the ledger is off/empty. Any ledger session not attributed to a known hub or
    worker is gathered into a trailing 'unattributed' pseudo-hub so nothing is lost.
    Fully defensive — every failure degrades to an empty list, never a broken board."""
    empty = {"hubs": [], "cost_thresholds": [0.01, 0.05]}
    try:
        import config as _cfg
        usage_on = _cfg.usage_tracking_enabled()
    except Exception:
        usage_on = False
    live = set(live_sids or [])
    by_sid = {}
    prompts_by_sid = {}
    reported_by_sid = {}
    all_session_costs = []
    try:
        for run in (task.get("runs") or []):
            sid = run.get("session_id")
            c = run.get("cost_usd")
            if sid and isinstance(c, (int, float)) and not isinstance(c, bool):
                reported_by_sid[sid] = reported_by_sid.get(sid, 0.0) + float(c)
        if usage_on:
            store = _backend()
            for r in store.session_usage_for_task(task["id"]):
                sid = r.get("session_id")
                if sid:
                    by_sid[sid] = r
            # The board prompt trail shows ONLY human-typed prompts, EACH followed by
            # Claude's last-bullet reply — the SAME machinery the markdown/CLI views use
            # (_human_prompts_with_replies: filters to human + one transcript read per
            # session for the reply). No cap → the full human trail flows into the cards.
            for p in _human_prompts_with_replies(_usage_engine().task_prompts(store, task)):
                sid = p.get("session_id")
                if not sid:
                    continue
                prompts_by_sid.setdefault(sid, []).append({
                    "ts": p.get("ts"), "kind": p.get("kind") or "prompt",
                    "text": p.get("text") or "", "role": p.get("role"),
                    "label": p.get("label"), "sid": p.get("sid"),
                    "human": True, "reply": p.get("reply") or "",
                })
    except Exception:
        return empty
    # per-session derived $ for the stdev cost bands (priced sessions only).
    for r in by_sid.values():
        s = _session_usage_summary(r)
        if s["cost_usd"] is not None:
            all_session_costs.append(s["cost_usd"])
    thresholds = _cost_band_thresholds(all_session_costs)

    try:
        tree = session_tree(task)
    except Exception:
        tree = {"hubs": [], "orphan_workers": []}
    meta = task.get("session_meta") or {}
    covered = set()

    def _worker_card(wk):
        sid = wk.get("sid") or ""
        covered.add(sid)
        summ = _session_usage_summary(by_sid.get(sid))
        d, model = wk.get("dir"), wk.get("model")
        resume = bg_aware_resume(sid, d) if (d and sid) else (
            ("cd %s && claude" % d) if d else None)
        prompts = list(prompts_by_sid.get(sid, []))
        # 3-tier liveness: running (a real live process, pid in live_sids), resumable
        # (a resume target exists but nothing is running), else linked.
        state = "running" if sid in live else ("resumable" if resume else "linked")
        return {
            "sid8": sid[:8], "session_id": sid, "label": wk.get("label") or "worker",
            "model": model, "live": sid in live, "state": state,
            "age": rel_time(wk.get("ts")),
            "resume_command": resume, "prompts": prompts,
            "prompt_count": len(prompts), "reported": reported_by_sid.get(sid, 0.0),
            **summ,
        }

    def _hub_card(sid, pinned, main, live_flag, msgs):
        covered.add(sid)
        m = meta.get(sid) or {}
        own = _session_usage_summary(by_sid.get(sid))
        wk_dicts = []
        # workers nested under this hub in the session tree (by spawner).
        for h in tree.get("hubs", []):
            if h.get("sid") == sid:
                wk_dicts = h.get("workers") or []
                break
        workers = [_worker_card(w) for w in wk_dicts]
        agg = _aggregate_usage_summary(
            [by_sid[s] for s in ([sid] + [w.get("sid") for w in wk_dicts])
             if s in by_sid])
        own_prompts = list(prompts_by_sid.get(sid, []))
        first_human = next((p["text"] for p in own_prompts if p["human"]), None)
        oneliner = (first_human or "attached · %s" % _tildify(m.get("cwd")))[:100]
        # hub prompts = its OWN prompts + all its workers' prompts, chronological.
        all_prompts = own_prompts + [p for w in workers for p in w["prompts"]]
        all_prompts.sort(key=lambda p: (p.get("ts") is None, p.get("ts") or 0))
        reported = reported_by_sid.get(sid, 0.0) + sum(w["reported"] for w in workers)
        # 3-tier liveness: running (a real live process — pid in live_sids), resumable
        # (has a transcript on disk, live_flag; resume works but nothing is running), else
        # linked (recorded, no live transcript). `live` stays as the strict RUNNING bit
        # so any legacy reader means "actually running", not "survived a crash".
        state = "running" if sid in live else ("resumable" if live_flag else "linked")
        return {
            "sid8": sid[:8], "session_id": sid, "pinned": pinned, "main": main,
            "live": sid in live, "state": state, "role": "hub", "msgs": msgs,
            "ordinal": m.get("ordinal"),
            "age": rel_time(m.get("ts")), "oneliner": oneliner,
            "resume_command": _session_resume_command(sid, m),
            "own": own, "workers": workers,
            "agg": agg, "reported": round(reported, 6),
            "prompts": all_prompts, "prompt_count": len(all_prompts),
        }

    cards = []
    for h in tree.get("hubs", []):
        cards.append(_hub_card(h.get("sid") or "", bool(h.get("pinned")),
                               bool(h.get("main")), bool(h.get("live")),
                               h.get("msgs") or 0))
    # main hub floats first, then pinned, then the rest (newest-first order preserved).
    cards.sort(key=lambda c: (0 if c["main"] else 1, 0 if c["pinned"] else 1))

    # Anything in the ledger not attributed to a hub or its workers → a trailing
    # 'unattributed' pseudo-hub, so no cost/prompt is silently dropped.
    orphans = [w for w in (tree.get("orphan_workers") or [])]
    leftover_sids = [s for s in by_sid if s not in covered
                     and s not in {w.get("sid") for w in orphans}]
    if orphans or leftover_sids:
        workers = [_worker_card(w) for w in orphans]
        leftover_rows = [by_sid[s] for s in leftover_sids]
        agg = _aggregate_usage_summary(
            leftover_rows + [by_sid[w["session_id"]] for w in workers
                             if w["session_id"] in by_sid])
        prompts = [p for w in workers for p in w["prompts"]]
        for s in leftover_sids:
            prompts += prompts_by_sid.get(s, [])
        prompts.sort(key=lambda p: (p.get("ts") is None, p.get("ts") or 0))
        if agg["mix"] or workers or prompts:
            cards.append({
                "sid8": "", "session_id": "", "pinned": False, "main": False,
                "live": False, "role": "unattributed", "msgs": 0, "age": "",
                "oneliner": "sessions with no recorded hub",
                "resume_command": None,
                "own": _aggregate_usage_summary(leftover_rows),
                "workers": workers, "agg": agg, "reported": 0.0,
                "prompts": prompts, "prompt_count": len(prompts),
            })
    return {"hubs": cards, "cost_thresholds": thresholds}


def stamp_closed(task):
    """Record the real moment a task entered a closed status. Called ONLY on the
    genuine open/active → closed transition (every close path) — a later ordinary
    update must not re-stamp it, so this is never called from touch/save."""
    task["closed_ts"] = _now()


def clear_closed(task):
    """Drop the closed timestamp — the task is being reopened, so it is no longer
    closed. Safe when never set."""
    task.pop("closed_ts", None)


# ---- session roster: hub ordinals + worker entries (task #463) ---------------
# Hub sessions are numbered <seq>-<n> (task["hub_ordinal_next"] is monotonic,
# never reused; -0 = the session that created the task). Workers keep their full
# descriptive name and NEVER get an ordinal. Everything lives inside the task
# blob (session_meta) — no store schema change; ensure_ordinals() is the lazy
# backfill for pre-roster tasks (oldest hub meta ts = the creator = 0).

def _next_hub_ordinal(task):
    n = int(task.get("hub_ordinal_next") or 0)
    task["hub_ordinal_next"] = n + 1
    return n

def ensure_ordinals(task):
    """Lazily backfill ordinals onto pre-roster hub session_meta entries, oldest
    ts first (the creator becomes -0). Returns True when anything changed (the
    caller persists). Idempotent; ignores role=='worker' entries."""
    meta = task.get("session_meta") or {}
    missing = [(sid, m) for sid, m in meta.items()
               if m.get("role") == "hub" and "ordinal" not in m]
    if not missing:
        return False
    missing.sort(key=lambda kv: kv[1].get("ts") or 0)
    for sid, m in missing:
        m["ordinal"] = _next_hub_ordinal(task)
    return True

def hub_ordinal(task, sid):
    m = (task.get("session_meta") or {}).get(sid) or {}
    if m.get("role") != "hub":
        return None
    if "ordinal" not in m:
        ensure_ordinals(task)
        m = (task.get("session_meta") or {}).get(sid) or {}
    return m.get("ordinal")

def ordinal_label(task, sid):
    """'<seq>-<n>' for a hub session of this task, or None (workers/unknown)."""
    n = hub_ordinal(task, sid)
    if n is None or task.get("seq") is None:
        return None
    return "%s-%s" % (task["seq"], n)

def session_display(task, sid):
    """Human handle for a session on this task: hub → '<seq>-<n>', worker → its
    slug name, else sid[:8]; '?' when absent."""
    if not sid:
        return "?"
    lab = ordinal_label(task, sid)
    if lab:
        return lab
    m = (task.get("session_meta") or {}).get(sid) or {}
    return m.get("name") or sid[:8]


def register_worker_session(task, sid, name=None, model=None, harness="claude",
                            status="running"):
    """Roster a WORKER session on the task record (full descriptive name, no
    ordinal). Re-registration updates status/model in place, preserving spawned_at."""
    if not sid:
        return
    meta = task.setdefault("session_meta", {})
    prev = meta.get(sid) or {}
    meta[sid] = {"ts": _now(), "role": "worker",
                 "name": name or prev.get("name"),
                 "model": model or prev.get("model"),
                 "harness": harness or prev.get("harness") or "claude",
                 "status": status,
                 "spawned_at": prev.get("spawned_at") or _now()}
    if sid not in task.setdefault("sessions", []):
        task["sessions"].append(sid)


def touch(task, session=None, note=None, reopen=False, register=True):
    task["updated_ts"] = _now()
    if reopen and is_closed(task):
        task["status"] = STATUS_OPEN          # reopening a closed task → open
        clear_closed(task)                    # …and it's no longer closed
    if session and register:
        if session not in task.get("sessions", []):
            task.setdefault("sessions", []).append(session)
        # Record where this session is running so /todo can later hand back a
        # `cd … && claude --resume …` one-liner that reopens it in the right dir.
        meta = task.setdefault("session_meta", {})
        prev = meta.get(session) or {}
        entry = {"cwd": os.getcwd(), "ts": _now(), "role": "hub",
                 "spawned_at": prev.get("spawned_at") or _now()}
        if prev.get("role") == "hub" and "ordinal" in prev:
            entry["ordinal"] = prev["ordinal"]        # re-touch keeps the number
        else:
            ensure_ordinals(task)                     # backfill olds FIRST so they
            entry["ordinal"] = _next_hub_ordinal(task)  # keep lower numbers
        if prev.get("preborn"):
            entry["preborn"] = True
        meta[session] = entry
    # Fold this activity moment into the time-in-task spans (idle-gap-capped).
    record_activity_span(task, task["updated_ts"])
    # register=False (cross-task scope updates) still ATTRIBUTES the log event to the
    # acting session — it just doesn't enroll that session as a worker on this task.
    add_log(task, note, session=session)


def set_status(task, status, note=None, session=None):
    """Move a task between the settable board states (open ⇄ active). Idempotent —
    returns True only if it changed, logging the transition so the activity trail
    shows when work began. Refuses anything outside open/active (returns False) —
    closing goes through /done, not here — so a typo never mislabels a task.
    `session` attributes the transition (log + status events) to its author."""
    if status not in STATUS_SETTABLE:
        return False
    prev = task_status(task)
    if prev == status:
        return False
    task["status"] = status
    if prev == STATUS_CLOSED:
        clear_closed(task)          # open/active from closed = a reopen
    if status == STATUS_ACTIVE:
        # Promotion to active = real work started (an edit here, or a delegate
        # --worktree worker via `status active`) → the digest may now be stale, and
        # this counts as one meaningful event toward the milestone staleness nudge.
        mark_digest_dirty(task)
        bump_digest_events(task)
    add_log(task, note or ("status → %s" % status), session=session)
    add_event(task, "status", "%s → %s" % (prev, status), session)
    return True


def close_task_inplace(task, note=None, session=None):
    """Close a task IN PLACE — the pure-dict half of the close path, safe to call
    inside a `store.mutate` mutator (no I/O, no save). Sets the status, stamps the
    real close moment, and logs + events the transition exactly as `/done` does.
    Returns True only if it changed; closing an already-closed task is a no-op.

    WHY THIS EXISTS rather than `set_status(task, STATUS_CLOSED)`: `set_status`
    deliberately REFUSES `closed` — it guards the settable open⇄active pair so a typo
    can never mislabel a task — and the two genuine close paths (`_close_one`,
    `cmd_done`) each hand-roll these same four lines before doing their OWN session
    and worker teardown. This is that shared core, named once.

    Callers that close as a SIDE EFFECT of a structural write (`--absorbed-by`,
    `--replaces`) use only this half and deliberately do NOT reap workers or detach
    sessions: neither verb means "this session is finished", and a structural
    declaration must not kill someone else's running worker."""
    prev = task_status(task)
    if prev == STATUS_CLOSED:
        return False
    task["status"] = STATUS_CLOSED
    stamp_closed(task)                      # the real moment it entered closed
    add_log(task, note or "closed", session=session)
    add_event(task, "status", "%s → %s" % (prev, STATUS_CLOSED), session)
    return True


def promote_active(task, note=None, session=None):
    """Promote an OPEN task to active because work has started. Idempotent — a
    no-op (returns False) when the task is already active or closed (an edit never
    resurrects a closed task)."""
    if task_status(task) != STATUS_OPEN:
        return False
    return set_status(task, STATUS_ACTIVE,
                      note=note or "auto-promoted to active (work started)",
                      session=session)


def _project_dir_for(cwd):
    """The session-transcript bucket Claude Code uses for a given launch cwd."""
    return os.path.join(PROJECTS_ROOT, cwd.replace("/", "-"))


# ---------------------------------------------------- transcript-derived caches ----
# A session transcript is APPEND-ONLY, and everything we derive from one — the user
# message count, the prompt→reply map — is a pure function of its bytes. So
# (st_mtime_ns, st_size) is a COMPLETE cache key: any change to the file changes one
# of them, which means a cache hit can never be stale. There is no invalidation
# window to reason about, and no need to guess whether a transcript is "done".
#
# This matters because the board asks the same questions over and over. Rendering
# 375 tasks over 458 transcripts called _session_msgcount 4072 times (one file was
# re-parsed 120 times) and _prompt_replies 571 times, each one re-reading a whole
# transcript: 2.37M json.loads for ~460 files' worth of information, and a Stop hook
# that blocked turn end for ~22s.
#
# Two layers:
#   • in-process — collapses the repeats WITHIN one render (4072 parses → 458).
#   • on-disk    — <data_dir>/cache/msgcounts.json, so a transcript that has not
#     changed is not re-parsed on the NEXT turn either. Counts ONLY: reply text is
#     prompt content and is never persisted, and the in-process layer already covers
#     the single render that needs it.
#
# Every layer is fail-open. This code runs inside the Stop hook, where an exception
# would block the user's turn, so a missing, malformed, or foreign cache file is
# ignored and the value simply recomputed. A cache is never a correctness dependency.

CACHE_DIR = "cache"                  # <data_dir>/cache — resolved per call (tests repoint DATA)
MSGCOUNT_CACHE_FILE = "msgcounts.json"
MSGCOUNT_CACHE_MAX = 4000            # entries kept on disk; least-recently-used dropped past this
MSGCOUNT_CACHE_TOUCH = 86400         # refresh an entry's last-used stamp at most once a day
REPLIES_CACHE_MAX = 256              # reply maps held in memory at once (bounds a big render)
MSGCOUNT_MEM_MAX = 8192              # in-memory counts kept — a growing transcript mints a
                                     # new key per append, and the MCP server is long-lived
SESSION_PATH_MEM_MAX = 8192          # resolved transcript paths kept, same reasoning

_MSGCOUNT_MEM = {}       # (path, mtime_ns, size) -> count             [this process]
_REPLIES_MEM = {}        # (path, mtime_ns, size) -> {uuid: reply}     [this process]
_SESSION_PATH_MEM = {}   # (projects_root, sid) -> resolved transcript [this process]
_MSGCOUNT_DISK = None    # {"file", "entries": {path: [mtime_ns, size, count, used]}, "dirty"}
_MSGCOUNT_ATEXIT = False


def _stat_key(path):
    """(st_mtime_ns, st_size) — a file's version identity — or None when it can't be
    stat-ed (missing, unreadable, or not a path at all). None means "no valid cache
    key", and every caller then reads the file unconditionally, so behaviour is
    exactly what it was before any cache existed."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


def _mem_put(cache, key, value, cap):
    """Insert into an in-process cache, dropping the oldest-inserted entry at `cap`.
    Insertion order is dict order (3.7+), so this is a plain FIFO — good enough for
    caches whose working set is one board render, and it keeps a long-lived process
    (the MCP server) from growing a key per transcript append forever."""
    if key not in cache and len(cache) >= cap:
        cache.pop(next(iter(cache)), None)
    cache[key] = value
    return value


def _msgcount_cache_path():
    """Resolved per call, against the DATA global — tests repoint it per test, and a
    moved CLAUDE_CONFIG_DIR must take its cache with it."""
    return os.path.join(DATA, CACHE_DIR, MSGCOUNT_CACHE_FILE)


def _msgcount_disk():
    """The on-disk count cache, as {"file", "entries", "dirty"}. Loaded once per
    process, and re-loaded if DATA is repointed (the old file is flushed first so
    nothing computed under it is lost).

    A malformed file, a foreign schema, a half-written entry: all silently yield an
    EMPTY cache. The cost of that is a recompute, which is the thing this file was
    only ever an optimisation for."""
    global _MSGCOUNT_DISK
    f = _msgcount_cache_path()
    cur = _MSGCOUNT_DISK
    if cur is not None and cur.get("file") == f:
        return cur
    if cur is not None:
        _msgcount_flush()                     # DATA moved → persist the previous file first
    entries = {}
    try:
        with open(f, encoding="utf-8") as fh:
            raw = json.load(fh)
        got = raw.get("entries") if isinstance(raw, dict) else None
        for k, v in (got or {}).items():
            # Every field must be an int: a truncated write or a hand-edit can leave
            # a partial row, and one bad row must not poison the rest of the cache.
            if (isinstance(k, str) and isinstance(v, list) and len(v) == 4
                    and all(isinstance(n, int) and not isinstance(n, bool) for n in v)):
                entries[k] = list(v)
    except (OSError, ValueError, TypeError, AttributeError):
        entries = {}
    _MSGCOUNT_DISK = {"file": f, "entries": entries, "dirty": False}
    return _MSGCOUNT_DISK


def _msgcount_flush():
    """Persist the count cache atomically. Best-effort by contract — it runs from an
    atexit handler inside a Stop hook, so every failure path is a silent return.

    Never CREATES the data dir: a cache flush is not what brings a store into being,
    and a test whose tmpdir has already been removed must not see it resurrected."""
    st = _MSGCOUNT_DISK
    if not st or not st.get("dirty"):
        return
    st["dirty"] = False                       # one attempt per dirty batch
    tmp = None
    try:
        entries = st["entries"]
        if len(entries) > MSGCOUNT_CACHE_MAX:              # evict least-recently-used
            entries = dict(sorted(entries.items(), key=lambda kv: kv[1][3],
                                  reverse=True)[:MSGCOUNT_CACHE_MAX])
            st["entries"] = entries
        f = st["file"]
        if not os.path.isdir(os.path.dirname(os.path.dirname(f))):   # <data_dir> gone
            return
        os.makedirs(os.path.dirname(f), exist_ok=True)
        tmp = "%s.tmp.%d" % (f, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "entries": entries}, fh)
        os.replace(tmp, f)
    except (OSError, ValueError, TypeError):
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _msgcount_persist_later():
    """Register the single atexit flush, lazily — a process that computes no new count
    never writes the cache file at all."""
    global _MSGCOUNT_ATEXIT
    if _MSGCOUNT_ATEXIT:
        return
    _MSGCOUNT_ATEXIT = True
    try:
        atexit.register(_msgcount_flush)
    except Exception:
        pass                                  # a cache that can't persist is still correct


def _session_msgcount(path):
    """Count of non-empty, non-system user messages in a transcript (0 if unreadable).

    Used to tell a real working session from an empty/stray one — size alone lies
    (a freshly-spawned empty session can still be several KB of system init).

    Cached on the transcript's (mtime, size) per the note above: in memory first,
    then <data_dir>/cache/msgcounts.json. The returned value — including the 0 for an
    unreadable file — is exactly what a direct parse returns. When the file cannot be
    stat-ed there is no key to cache under, so the parse runs as it always did."""
    key = _stat_key(path)
    if key is None:
        return _session_msgcount_uncached(path)
    memkey = (path, key[0], key[1])
    hit = _MSGCOUNT_MEM.get(memkey)
    if hit is not None:
        return hit
    disk = _msgcount_disk()
    ent = disk["entries"].get(path)
    if ent is not None and ent[0] == key[0] and ent[1] == key[1]:
        now = int(_now())
        if now - ent[3] > MSGCOUNT_CACHE_TOUCH:     # keep LRU honest at ~1 write/day
            ent[3] = now
            disk["dirty"] = True
            _msgcount_persist_later()
        return _mem_put(_MSGCOUNT_MEM, memkey, ent[2], MSGCOUNT_MEM_MAX)
    n = _session_msgcount_uncached(path)
    disk["entries"][path] = [key[0], key[1], n, int(_now())]
    disk["dirty"] = True
    _msgcount_persist_later()
    return _mem_put(_MSGCOUNT_MEM, memkey, n, MSGCOUNT_MEM_MAX)


def _session_msgcount_uncached(path):
    """The actual parse behind _session_msgcount — one full pass over the transcript.
    Call the cached front door instead unless you are deliberately measuring."""
    n = 0
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    t = c
                elif isinstance(c, list):
                    t = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                else:
                    t = ""
                t = t.strip()
                if t and not t.startswith("<"):
                    n += 1
    except OSError:
        return 0
    return n


def _find_session_path(sid):
    """Locate a session's transcript across ALL project buckets.

    A session's bucket is its LAUNCH cwd, which can differ from whatever cwd /todo
    happened to record (e.g. you launched from ~ but cd'd into a worktree before the
    task was touched). So we search every bucket by session id rather than trusting
    the recorded cwd. Returns the `.jsonl` path, or None.

    A resolved path is memoized per process (keyed by the projects root, which tests
    repoint) and re-checked with ONE os.path.exists on each hit — that turns a
    listdir-plus-N-stats scan into a single stat, which is what the board's thousands
    of lookups actually cost. Only SUCCESSES are cached, so a transcript that appears
    later is still found; a cached path that disappears falls back to a full rescan,
    and every consumer pairs the path with a content read that fails closed anyway."""
    memkey = (PROJECTS_ROOT, sid)
    hit = _SESSION_PATH_MEM.get(memkey)
    if hit is not None and os.path.exists(hit):
        return hit
    try:
        buckets = os.listdir(PROJECTS_ROOT)
    except OSError:
        return None
    for b in buckets:
        p = os.path.join(PROJECTS_ROOT, b, sid + ".jsonl")
        if os.path.exists(p):
            return _mem_put(_SESSION_PATH_MEM, memkey, p, SESSION_PATH_MEM_MAX)
    return None


def estimate_session_tokens(session):
    """A cheap, rough proxy for how full the context window is: the session
    transcript's byte size // 4 (~chars-per-token). Uses os.path.getsize — no read —
    so it stays fast on a large transcript, and it errs slightly EARLY (which is what
    we want for a pre-compaction nudge). Returns 0 when the transcript can't be found.
    Feeds the proactive context-pressure Stop nudge (see cmd_stop_nudge)."""
    if not session:
        return 0
    path = _find_session_path(session)
    if not path:
        return 0
    try:
        return os.path.getsize(path) // 4
    except OSError:
        return 0


# How much of the transcript tail measure_context_tokens reads. The live context
# size lives on the LAST assistant message's usage block, which is near the end of
# the file, so a bounded tail read keeps this cheap even on a multi-MB transcript.
_USAGE_TAIL_BYTES = 256 * 1024
# The usage fields that make up the RESIDENT context (what's actually re-sent to the
# model each turn). output_tokens is deliberately EXCLUDED — generated output isn't
# part of the next turn's context window.
_CONTEXT_USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _extract_usage(o):
    """Pull the `usage` dict out of one decoded transcript line, whether it's nested
    under `message` (Claude Code records assistant turns as {"message": {..., "usage":
    {...}}}) or sits at the top level. None when there's no usage dict."""
    if not isinstance(o, dict):
        return None
    msg = o.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
        return msg["usage"]
    if isinstance(o.get("usage"), dict):
        return o["usage"]
    return None


def measure_context_tokens(session):
    """The CURRENT context-window occupancy in tokens, read from the session's real
    transcript — the authoritative signal the proactive checkpoint_pct nudge uses.

    Claude Code writes a `usage` block on each assistant message; the live context
    size is the MOST RECENT one's input_tokens + cache_read_input_tokens +
    cache_creation_input_tokens (output_tokens are NOT resident context). We read only
    the file TAIL (last _USAGE_TAIL_BYTES) and scan its lines in REVERSE, so a huge
    transcript costs a small bounded read; a sliced-off partial first line and any
    half-written/malformed record are skipped rather than crashing. Returns 0 when
    nothing usable is found so callers fall back to estimate_session_tokens()."""
    if not session:
        return 0
    path = _find_session_path(session)
    if not path:
        return 0
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _USAGE_TAIL_BYTES:
                f.seek(size - _USAGE_TAIL_BYTES)   # tail only — the newest usage is here
            chunk = f.read()
    except OSError:
        return 0
    # Reverse-scan the tail: the first line that parses AND carries a usage block wins.
    for line in reversed(chunk.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line or '"usage"' not in line:      # cheap skip before the JSON parse
            continue
        try:
            usage = _extract_usage(json.loads(line))
        except ValueError:
            continue                               # malformed/partial line — skip it
        if not usage:
            continue
        total = 0
        for k in _CONTEXT_USAGE_KEYS:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                total += int(v)
        if total > 0:
            return total
    return 0


def session_model(session):
    """The model id the session is currently running, read from the most recent
    assistant message in the transcript (`message.model`) — used to size the context
    window (pricing.context_window_for) so the checkpoint %/tokens are model-aware. Same
    bounded tail read as measure_context_tokens. Returns "" when nothing usable is found
    (callers then fall back to the 200k default)."""
    if not session:
        return ""
    path = _find_session_path(session)
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > _USAGE_TAIL_BYTES:
                f.seek(size - _USAGE_TAIL_BYTES)
            chunk = f.read()
    except OSError:
        return ""
    for line in reversed(chunk.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line or '"model"' not in line:      # cheap skip before the JSON parse
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get("message") if isinstance(o, dict) else None
        model = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
    return ""


def _settings_model(path):
    """`.model` from a Claude Code settings.json at `path`, or "" if the file is
    missing / unparseable / carries no string model. Read defensively — settings
    files are user-editable and may be absent or malformed."""
    try:
        with open(path) as f:
            m = json.load(f).get("model")
    except (OSError, ValueError, AttributeError):
        return ""
    return m.strip() if isinstance(m, str) else ""


def claude_code_model_selection():
    """The effective Claude Code model SELECTION string — which, unlike the
    transcript's `message.model`, still carries the `[1m]` 1M-context marker (Claude
    Code strips it before calling the API, so the transcript never records it).

    First non-empty wins: ANTHROPIC_MODEL env → ~/.claude/settings.local.json →
    ~/.claude/settings.json. Returns "" when nothing is found (callers then fall back
    to the transcript model / default window)."""
    env = os.environ.get("ANTHROPIC_MODEL")
    if env and env.strip():
        return env.strip()
    home = os.path.expanduser("~")
    for name in ("settings.local.json", "settings.json"):
        m = _settings_model(os.path.join(home, ".claude", name))
        if m:
            return m
    return ""


_MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")


def _model_family_token(model_id):
    """The family token ({"opus","sonnet","haiku","fable"}) found in a model id, or
    None when the id carries no recognizable family."""
    mid = (model_id or "").lower()
    for fam in _MODEL_FAMILIES:
        if fam in mid:
            return fam
    return None


def _same_model_family(a, b):
    """True only when both ids resolve to the SAME recognizable family. If EITHER id
    has no family token, be conservative and return False — so we never inflate the
    window on an unknown id (e.g. a `--model sonnet` session under an `opus[1m]`
    default must NOT be upgraded to 1M)."""
    fa, fb = _model_family_token(a), _model_family_token(b)
    return fa is not None and fa == fb


def _read_statusline_stdin():
    """The statusLine JSON piped on stdin (or {} when absent). NEVER blocks: reads only
    when stdin is a non-tty pipe; tolerates empty/malformed input. Used by the
    --statusline provider path to capture the harness context-window size."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
    except Exception:
        return {}
    if not data or not data.strip():
        return {}
    try:
        d = json.loads(data)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def persist_harness_context_window(session, size):
    """Merge the harness-reported context-window SIZE into the per-session HUD snapshot
    (the same file harness_context_window reads and hud.observe writes). Best-effort and
    atomic; a non-positive / unparseable size, or a value equal to what is already
    stored, is a no-op (so the common per-render case does zero IO). This is the
    HUD-INDEPENDENT capture path: the statusline payload is the ONLY channel Claude Code
    exposes context_window_size on (no hook receives it), so the always-on --statusline
    provider persists it here even when the cost HUD is off."""
    if not session:
        return
    try:
        size = int(size)
    except (TypeError, ValueError):
        return
    if size <= 0:
        return
    try:
        import paths
        d = os.path.join(paths.data_dir(), "hud")
        p = os.path.join(d, "%s.json" % session)
        try:
            with open(p) as f:
                snap = json.load(f)
            if not isinstance(snap, dict):
                snap = {}
        except (OSError, ValueError):
            snap = {}
        if snap.get("context_window_size") == size:
            return
        snap["context_window_size"] = size
        os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
        os.replace(tmp, p)
    except OSError:
        pass


def harness_context_window(session):
    """The context-window size (tokens) the HARNESS ITSELF reported for `session`,
    read from the HUD snapshot (the statusline payload's
    `context_window.context_window_size`, persisted by hud.observe). This is the
    authoritative, 1M-aware window: a runtime `/model` selection carries a `[1m]`
    marker that the transcript (message.model) and settings.json/ANTHROPIC_MODEL never
    record, but the statusline payload's size does. Returns an int > 0, or None when
    the HUD has not captured a size yet (HUD off, or no render this session) — callers
    then fall back to model-id derivation. Never raises."""
    if not session:
        return None
    try:
        import paths
        p = os.path.join(paths.data_dir(), "hud", "%s.json" % session)
        with open(p) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        n = int(d.get("context_window_size"))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def effective_context_window(session):
    """The context-window size (tokens) to size the checkpoint %/tokens math against
    for `session`. Precedence: an EXPLICIT override (TASK_STATION_CONTEXT_WINDOW / the
    `context_window` config key) always wins; else the HARNESS-reported window
    (harness_context_window — the statusline payload's context_window_size, which is
    already 1M-aware) is authoritative; else it is derived from the transcript model and
    UPGRADED to the Claude Code selection's 1M window only when the selection is
    genuinely larger AND of the same model family; else 200k. This is why an Opus-1M
    session (transcript records the marker-stripped `claude-opus-4-8`, and settings/env
    carry no marker for a runtime `/model` pick) is still sized at 1,000,000."""
    import config
    tm = session_model(session)                 # transcript base id, marker stripped
    win = config.context_window(tm)             # explicit override wins in here
    # An explicit override (env or config key, positive int) is user intent — never
    # inflate/deflate past it; return immediately.
    raw = os.environ.get("TASK_STATION_CONTEXT_WINDOW")
    if raw is None:
        raw = config.get("context_window", None)
    if raw is not None:
        try:
            if int(raw) > 0:
                return win
        except (TypeError, ValueError):
            pass
    # No user override: the harness's OWN reported window is authoritative — it already
    # reflects a 1M runtime selection that the transcript/settings never record.
    hw = harness_context_window(session)
    if hw and hw > 0:
        return hw
    # No HUD data: fall back to the CC model-selection marker, else the transcript model.
    cm = claude_code_model_selection()          # selection, may carry [1m]
    if cm:
        import pricing
        if pricing.context_window_for(cm) > pricing.context_window_for(tm) \
                and _same_model_family(tm, cm):
            win = config.context_window(cm)
    return win


def _session_cwd(path):
    """The cwd a session was launched in, read from the transcript itself —
    authoritative (Claude Code records it on every entry), and decode-free (we never
    have to reverse the lossy bucket-name encoding). None if unreadable/absent."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _load_delegate_registry():
    try:
        with open(DELEGATE_REGISTRY) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---- bg-aware resume commands (task #464) ------------------------------------
# A bare `claude --resume <sid>` is REFUSED by Claude Code when <sid> is a
# currently-live `--bg`/background agent ("currently running as a background
# agent; use `claude agents` … or --fork-session"). So every resume one-liner we
# emit routes through bg_aware_resume(): live-bg → `claude agents` (ATTACH the exact
# live session — the whole point of -s/resume is to land IN it, not a copy),
# otherwise today's bare `--resume`. The confirmation text additionally surfaces the
# fork path (`--fork-session`) as an aside via bg_resume_hint() for anyone who wants
# to branch a copy instead.

def _live_bg_index():
    """{sessionId: row} of currently-live background agents from `claude agents
    --json` (via the delegate harness adapter), snapshotted for this process. {} on
    any failure so a missing/again-unavailable `claude` degrades to today's bare
    `--resume`. A task-station process is one CLI invocation (board/detail render),
    so a process-lifetime snapshot is the right granularity."""
    global _LIVE_BG_INDEX
    if _LIVE_BG_INDEX is not None:
        return _LIVE_BG_INDEX
    idx = {}
    # TASK_STATION_NO_AGENT_QUERY lets the test suite (and any headless caller that
    # must not shell out) skip the live `claude agents` probe deterministically.
    if not os.environ.get("TASK_STATION_NO_AGENT_QUERY"):
        try:
            dg = _delegate_module()
            if dg is not None:
                idx = dg.harness.ClaudeAdapter().agents_index() or {}
        except Exception:
            idx = {}
    _LIVE_BG_INDEX = idx if isinstance(idx, dict) else {}
    return _LIVE_BG_INDEX


def _is_live_bg(sid):
    """True when `sid` is a currently-live background agent — a background-KIND row
    with a pid; interactive sessions resume with a bare --resume. Tolerates a stored
    SHORT id via unique-prefix match (mirrors ClaudeAdapter.worker_status). False on
    absence/no-pid/any failure."""
    if not sid:
        return False
    idx = _live_bg_index()
    row = idx.get(sid)
    if row is None:
        hits = [r for s, r in idx.items() if s.startswith(sid)]
        row = hits[0] if len(hits) == 1 else None
    return bool(row) and row.get("kind") == "background" and bool(row.get("pid"))


def bg_aware_resume(sid, cwd=None):
    """The resume one-liner for `sid`, BG-aware.

    When `sid` is a currently-live background agent a bare `--resume` is REFUSED, and
    the POINT of a resume is to land in the EXACT live session — so emit `claude
    agents` to ATTACH it (never a `--fork-session` copy, which would silently branch a
    pointless duplicate). Otherwise return today's bare `[cd <cwd> && ]claude --resume
    <sid>`. `cwd` prefixes `cd <cwd> && ` on the bare-resume path only (attaching is
    global — no cd needed). None when `sid` is falsy."""
    if not sid:
        return None
    if _is_live_bg(sid):
        return "claude agents"
    prefix = ("cd %s && " % cwd) if cwd else ""
    return "%sclaude --resume %s" % (prefix, sid)


def bg_resume_hint(sid, cwd=None):
    """Human hint when `sid` is a live background agent: the resume attaches the live
    session (`claude agents`); mention forking a copy only as an ASIDE for anyone who
    wants to branch instead. None when `sid` isn't live-bg (nothing special to
    surface)."""
    if not _is_live_bg(sid):
        return None
    return ("attach the live session: `claude agents`  ·  to branch a copy instead: "
            "`claude --resume %s --fork-session`" % sid)


def worker_targets(task):
    """Structured resume entries for the in-project workers this task has delegated
    into: a list of `{label, command, note}` dicts (command is None when no worker
    dir is recorded yet). The SINGLE source for both `worker_lines()` (the terminal
    task-detail) and the HTML board's de-emphasised Workers subsection, so they match.

    Worker session-ids are read LIVE from the delegate registry (keyed
    <seq>:<project>[:<label>]) so they reflect delegate's own self-healing rather
    than a stale snapshot. Lists the default worker per repo plus any labelled
    concurrent ones. Returns [] when the task has no recorded projects."""
    projects = task.get("projects") or []
    if not projects:
        return []
    reg = _load_delegate_registry()
    seq = task.get("seq")
    out = []
    for p in projects:
        base = "%s:%s" % (seq, p)
        keys = sorted(k for k in reg if k == base or k.startswith(base + ":"))
        if not keys:
            out.append({"label": p, "command": None, "note": "no worker recorded yet"})
            continue
        for k in keys:
            e = reg.get(k, {})
            d, sid, model = e.get("dir"), e.get("session_id"), e.get("model")
            disp = p if k == base else "%s [%s]" % (p, e.get("label") or k.split(":", 2)[-1])
            if d and sid:
                out.append({"label": disp,
                            "command": bg_aware_resume(sid, d),
                            "note": None, "model": model, "session": sid})
            elif d:
                out.append({"label": disp, "command": "cd %s && claude" % d,
                            "note": "no active worker yet", "model": model, "session": sid})
            else:
                out.append({"label": disp, "command": None,
                            "note": "no worker recorded yet", "model": model, "session": sid})
    return out


def worker_lines(task, live=None):
    """The terminal-detail rendering of `worker_targets()` — aligned
    `    <repo>   <resume command>  · <model>` lines (model shown when the registry
    recorded one; a `(note)` suffix when there's no live worker yet). When `live`
    (a `{session_id: row}` index from _live_session_index()) is given, a worker
    whose session is actually running gets a `● busy/idle · age` annotation.
    Returns [] when the task has no recorded projects."""
    live = live or {}
    out = []
    for w in worker_targets(task):
        model = ("  · %s" % w["model"]) if w.get("model") else ""
        note = _live_note(live.get(w.get("session")))
        if w["command"] and not w["note"]:
            out.append("    %-22s %s%s%s" % (w["label"], w["command"], model, note))
        elif w["command"]:
            out.append("    %-22s %s%s   (%s)%s" % (w["label"], w["command"], model, w["note"], note))
        else:
            out.append("    %-22s (%s)" % (w["label"], w["note"]))
    return out


def _is_resumable(cmd):
    """True when `cmd` already targets a CONCRETE session — a live `--resume` or a
    pre-bound `--session-id`. The jump path uses this to decide whether to use the
    command as-is or mint a fresh session; a descriptive "start fresh" line (bare
    `claude`) or None is NOT resumable."""
    return bool(cmd) and ("--resume " in cmd or "--session-id " in cmd)


def _fresh_session_cwd(meta):
    """Best cwd for a freshly minted session: the most recently recorded session's
    cwd (so the new window opens where the work lives), else the process cwd."""
    for m in sorted((meta or {}).values(), key=lambda m: m.get("ts", 0), reverse=True):
        if m.get("cwd"):
            return m["cwd"]
    return os.getcwd()


def fresh_resume_command(task, preborn=False):
    """Mint a brand-new session id, pre-bind it to `task`, and return
    `(sid, "cd <cwd> && claude --session-id <sid>")`.

    Pre-binding = a hub `session_meta` entry + a session→task link, so when the
    emitted command launches the window, SessionStart sees the link and
    auto-attaches it. This MINTS a uuid, so it is called ONLY from paths that
    actually open a window (the `-s` jump, `pin --new`) — never from the pure
    display path (`resume_command`), which must not mint on every render.

    `preborn=True` marks the meta entry so `resume_command` will emit
    `--session-id <sid>` for it (used by `pin --new`) until its transcript exists."""
    new_sid = str(uuid.uuid4())
    meta = task.setdefault("session_meta", {})
    cwd = _fresh_session_cwd(meta)
    ensure_ordinals(task)
    entry = {"cwd": cwd, "ts": _now(), "role": "hub",
             "ordinal": _next_hub_ordinal(task), "spawned_at": _now()}
    if preborn:
        entry["preborn"] = True
    meta[new_sid] = entry
    if new_sid not in task.setdefault("sessions", []):
        task["sessions"].append(new_sid)
    save_task(task)
    set_link(new_sid, task["id"])
    return new_sid, "cd %s && claude --session-id %s" % (cwd, new_sid)


def _resume_target(task, current_session=None):
    """Structured form of the hub resume one-liner: a dict
    `{command, session, cwd, ts, pinned, fresh}` for the session that holds this
    task's context, or None when no sessions are recorded. `resume_command()` is a
    thin wrapper that returns just `.command`, so this is the SINGLE source of truth
    for the resume line — the terminal task-detail and the HTML board read the same
    computation (and the same self-corrected cwd). `ts` is the resumed transcript's
    last-activity epoch (mtime), or the recorded meta ts for the fresh-start/preborn
    fallbacks; `pinned` mirrors whether the task has a pinned session; `fresh` is True
    only for the no-live-transcript fresh-start fallback.

    GUARANTEE: only ever resumes one of THIS task's own recorded sessions — never
    another task's. (Critical: every hub shares the home bucket, so a whole-bucket
    fallback or `claude --continue` could resume an unrelated task. We never do that.)

    SELF-CORRECTING cwd: the resume directory comes from the *transcript itself* (its
    recorded launch cwd), located by searching every bucket for the session id — NOT
    from whatever cwd /todo happened to capture. So a session is still found and
    resumed correctly even if it was recorded against the wrong directory (e.g. you
    launched from ~ but cd'd into a worktree before the task was touched). Prefers the
    most recent SUBSTANTIVE session (so merely opening `/todo <n>` to look — a 1-2
    message session — never displaces the real working session); if none of the
    task's sessions have a findable live transcript, starts fresh."""
    meta = task.get("session_meta") or {}
    if not meta:
        return None
    pinned = bool(task.get("pinned_session"))
    # The resumed window tints itself on attach via the SessionStart hook
    # (cmd_session_tint → tint_escape), so the resume command stays a clean
    # `cd … && claude …` with no tint prefix.
    # An explicit pin wins (PK-style): always resume that exact session, with the cwd
    # self-corrected from its transcript. Falls through to the heuristic only if the
    # pinned session has no findable live transcript (so a stale pin can't strand you).
    pin = task.get("pinned_session")
    if pin:
        path = _find_session_path(pin)
        if path and _session_msgcount(path) >= 1:
            cwd = _session_cwd(path) or (meta.get(pin) or {}).get("cwd")
            if cwd:
                return {"command": bg_aware_resume(pin, cwd),
                        "session": pin, "cwd": cwd, "ts": os.path.getmtime(path),
                        "pinned": True, "fresh": False}
        # A pin deliberately pre-bound to an UNBORN session (`pin --new`) has no
        # transcript yet. Honour it anyway by emitting `--session-id <pin>` so the
        # window that opens BECOMES that session — stays PURE (the uuid already
        # exists; nothing is minted here). Once it's born, the branch above wins.
        pm = meta.get(pin) or {}
        if pm.get("preborn"):
            cwd = pm.get("cwd") or os.getcwd()
            return {"command": "cd %s && claude --session-id %s" % (cwd, pin),
                    "session": pin, "cwd": cwd, "ts": pm.get("ts"),
                    "pinned": True, "fresh": False}
    hubs = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    pool = hubs or list(meta.items())
    # For each of THIS task's sessions, find its transcript ANYWHERE and read the
    # cwd from the transcript — independent of the (possibly wrong) recorded cwd.
    # SKIPPED sessions (link == SKIP_SENTINEL) are deliberately untracked and must
    # NEVER be a resume target, even with a live transcript.
    live = []
    for sid, m in pool:
        if get_link(sid) == SKIP_SENTINEL:
            continue
        path = _find_session_path(sid)
        if not path:
            continue
        msgs = _session_msgcount(path)
        if msgs >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                live.append((sid, cwd, os.path.getmtime(path), msgs))
    # The current session is NEVER a valid `-s` target: resuming the very
    # conversation you jumped from is the tainting bug. Exclude it HARD (no
    # fallback to it) — if nothing else remains, fall through to fresh-start.
    live = [x for x in live if x[0] != current_session]
    if live:
        # Prefer SUBSTANTIVE sessions: a session that merely ran `/todo <n>` to look
        # has 1-2 messages and must not displace the real working session. Among
        # sessions past a small substance floor, take the most recent; only if none
        # clear the floor do we fall back to the most recent of any.
        cands = [x for x in live if x[3] >= SUBSTANCE_FLOOR] or live
        cands.sort(key=lambda x: x[2], reverse=True)   # newest transcript first
        sid, cwd, mtime, _ = cands[0]
        return {"command": bg_aware_resume(sid, cwd),
                "session": sid, "cwd": cwd, "ts": mtime,
                "pinned": pinned, "fresh": False}
    # No findable live transcript for any recorded session → fresh start
    # (NEVER --continue, which in the shared home bucket could resume a different task).
    pool.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    for sid, m in pool:
        if m.get("cwd"):
            return {"command": ("cd %s && claude   # no live session found — starting fresh; "
                                "re-attach with /todo %s" % (m["cwd"], task.get("seq", ""))),
                    "session": None, "cwd": m["cwd"], "ts": m.get("ts"),
                    "pinned": pinned, "fresh": True}
    return None


def resume_command(task, current_session=None):
    """`cd <dir> && claude …` for the HUB session that holds this task's context, or
    None when no sessions are recorded. Thin wrapper over `_resume_target` (which
    documents the full guarantee + self-correcting-cwd behaviour); returns only the
    command string so existing callers are unchanged."""
    r = _resume_target(task, current_session)
    return r["command"] if r else None


# ---- WS3: session tree + task relations (detail + board consume these) -------

def session_tree(task):
    """The hub/worker session TREE for one task, for `/todo <n>` detail and the board.

    Returns `{"hubs": [hub…], "orphan_workers": [wk…]}` where
      hub = {"sid", "cwd", "ts", "pinned", "main", "live", "msgs", "preborn", "workers": [wk…]}
      wk  = {"sid", "project", "label", "dir", "model", "ts", "spawner"}

    Hubs are the `session_meta` entries with `role == "hub"`, newest-first by their
    recorded ts. `live` = a transcript is findable AND has ≥1 user message; `msgs` is
    that count (0 when no transcript). `main` = the pinned hub if one is pinned, else
    the newest live hub with `msgs >= SUBSTANCE_FLOOR` (fallback: newest live) — every
    other hub is a side-quest. Workers are the delegate-registry entries whose `seq`
    matches this task, nested under the hub whose sid == their `spawner` (WS2 field);
    a worker whose spawner is absent or names no known hub falls to `orphan_workers`.
    Degrades gracefully: absent registry / absent `spawner` → workers listed unnested."""
    meta = task.get("session_meta") or {}
    pinned = task.get("pinned_session")
    # Hub entries, newest-first.
    hub_items = [(sid, m) for sid, m in meta.items() if m.get("role") == "hub"]
    hub_items.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
    hub_sids = {sid for sid, _ in hub_items}
    # Workers for this task (seq-matched), grouped by spawning hub.
    workers_by_spawner = {}
    orphan_workers = []
    seq = task.get("seq")
    if seq is not None:
        seq_s = str(seq)
        reg = _load_delegate_registry()
        for e in reg.values():
            if str(e.get("seq")) != seq_s:
                continue
            wk = {"sid": e.get("session_id"), "project": e.get("project"),
                  "label": e.get("label"), "dir": e.get("dir"),
                  "model": e.get("model"), "ts": e.get("ts"),
                  "spawner": e.get("spawner")}
            sp = e.get("spawner")
            if sp and sp in hub_sids:
                workers_by_spawner.setdefault(sp, []).append(wk)
            else:
                orphan_workers.append(wk)
    _wk_sort = lambda w: w.get("ts") or 0
    hubs = []
    for sid, m in hub_items:
        path = _find_session_path(sid)
        msgs = _session_msgcount(path) if path else 0
        hubs.append({
            "sid": sid, "cwd": m.get("cwd"), "ts": m.get("ts"),
            "pinned": (sid == pinned), "main": False,
            "live": bool(path) and msgs >= 1, "msgs": msgs,
            "preborn": bool(m.get("preborn")), "ordinal": m.get("ordinal"),
            "workers": sorted(workers_by_spawner.get(sid, []), key=_wk_sort, reverse=True),
        })
    # Classify the single "main" hub (the working session); the rest are side-quests.
    main = None
    if pinned:
        main = next((h for h in hubs if h["sid"] == pinned), None)
    if main is None:
        live_hubs = [h for h in hubs if h["live"]]        # hubs already newest-first
        subst = [h for h in live_hubs if h["msgs"] >= SUBSTANCE_FLOOR]
        main = (subst or live_hubs or [None])[0]
    if main is not None:
        main["main"] = True
    return {"hubs": hubs, "orphan_workers": sorted(orphan_workers, key=_wk_sort, reverse=True)}


def related_edges(task, tasks=None, semantic=False):
    """Task-to-task relation edges for one task, both directions.

    Returns `{"out": [edge…], "in": [edge…]}`. `out` = this task's own `related`
    list (spawned-from / related edges it stored); `in` = reverse edges DERIVED by
    scanning other tasks' `related` lists for entries pointing at `task["id"]`. Each
    edge is annotated with the OTHER task's current `status` (None when that task is
    gone). Pass `tasks` (a task-blob list) to reuse a single load — the board builds
    it once so the scan stays O(N); detail leaves it None and scans `all_tasks()`.

    `semantic=True` adds a third key, `"semantic"` (the derived `touches-same` edges
    from `semantic_edges()`); default False keeps the return shape byte-identical to
    every existing caller (detail render / tests are unaffected)."""
    scan = tasks if tasks is not None else all_tasks()
    by_id = {t.get("id"): t for t in scan}
    tid = task.get("id")
    out = []
    for e in task.get("related") or []:
        other = by_id.get(e.get("id"))
        out.append({"seq": e.get("seq"), "id": e.get("id"), "kind": e.get("kind"),
                    "ts": e.get("ts"), "status": task_status(other) if other else None})
    inn = []
    for o in scan:
        if o.get("id") == tid:
            continue
        for e in o.get("related") or []:
            if e.get("id") == tid:
                inn.append({"seq": o.get("seq"), "id": o.get("id"),
                            "kind": e.get("kind"), "status": task_status(o)})
    result = {"out": out, "in": inn}
    if semantic:
        result["semantic"] = semantic_edges(task, scan)
    return result


# --- canonical relation resolution -------------------------------------------
#
# A task↔task relationship is ONE thing, but the store can record it up to three
# times: this task's `related` list holds it, the OTHER task's list holds the mirror
# (a reciprocal pair), and — because `append_related` dedups on id+KIND — the same
# side may hold it twice under two different kinds. Every consumer that walked the
# out edges and then the in edges therefore printed the same counterpart two or
# three times. `canonical_relations` is the ONE resolver they all route through.
#
# The dedup key is THE OTHER TASK'S id alone, never (id, kind): keying on the kind
# is exactly what leaves the mixed-label duplicate standing, since the two records
# differ precisely in their kind.

# Label precedence when one pair carries several kinds — LOWEST rank wins. A
# specific claim outranks a vague one: `related` explicitly claims nothing, so it is
# weakest. This is the LABEL axis and is deliberately NOT the graph's
# visual-prominence axis (where spawned-from is the dimmest edge) — different
# questions.
#
# The order, and why:
#   depends-on   an execution-order claim — it gates when work can start, so it is
#                the most consequential thing a pair can be.
#   parent       structural containment; every roll-up is computed over it.
#   absorbed-by  a lifecycle verdict that also TRANSFERS work: mine became theirs.
#   replaces     the same terminal verdict without the transfer — their approach was
#                dropped for mine. Ranks just under absorbed-by because it settles the
#                pair just as firmly but carries nothing across.
#   duplicates   names the collision without settling it: same work, no statement about
#                which survives. Weaker than either verdict, far stronger than `related`.
#   spawned-from a HISTORICAL fact about where a task came from, not what the pair is
#                now — which is why both verdicts and even `duplicates` outrank it.
#   related      claims nothing at all.
_REL_KIND_RANK = {"depends-on": 0, "parent": 1, "absorbed-by": 2, "replaces": 3,
                  "duplicates": 4, "spawned-from": 5, "related": 6}
_REL_KIND_RANK_UNKNOWN = 99


def _rel_kind_rank(kind):
    """Precedence rank for a relation kind — lowest wins. An unknown/future kind
    sorts last but never raises, so a store written by a newer version degrades to
    "shown, ranked last" instead of breaking a render."""
    return _REL_KIND_RANK.get(kind, _REL_KIND_RANK_UNKNOWN)


def canonical_relations(task, tasks=None, rev=None, edges=None):
    """ONE relationship per other task, deduped on the OTHER TASK's id.

    Returns a list of `{"id", "seq", "kind", "dir", "status"}` where `dir` is
    "out" (this task stored the edge) or "in" (derived from the other side). The
    shared resolver behind the `Related:` detail line, the board card's relation
    row and the graph's lineage precedence, so all three agree about what a pair IS.

    The rules, in force order:

      * dedup key = the other task's `id`; an entry carrying no id falls back to a
        `("seq", n)` key so nothing is ever silently dropped;
      * lowest `_REL_KIND_RANK` wins the label — a pair recorded as BOTH
        `spawned-from` and `related` (legal: `append_related` dedups on id+kind)
        resolves to `spawned-from`, once;
      * on an equal kind, `dir="out"` beats `dir="in"` — the side that asserted it
        wins;
      * a self-edge is dropped, matching `_add_undirected`;
      * order is kind rank, then the other task's seq — two runs over one store
        produce identical output.

    Where the raw edges come from, in precedence order: an already-built `edges`
    (a `related_edges`-shaped `{"out": […], "in": […]}`); else `rev`, this task's
    reverse-edge list out of the board's O(1) rev_map, with the out side read
    straight off `task["related"]`; else a `related_edges(task, tasks)` scan. Pure
    apart from that scan."""
    if edges is None:
        if rev is None:
            edges = related_edges(task, tasks)
        else:
            edges = {"out": [{"seq": r.get("seq"), "id": r.get("id"),
                              "kind": r.get("kind")}
                             for r in (task.get("related") or [])],
                     "in": list(rev)}

    def _pick(e):
        """Which recorded edge REPRESENTS the pair: kind first, then out beats in."""
        return (_rel_kind_rank(e.get("kind")), 0 if e.get("dir") == "out" else 1)

    def _order(e):
        """Display order: kind rank, then the counterpart's seq (seq-less last),
        then its id — a total order, so the output never wobbles between runs."""
        seq = e.get("seq")
        return (_rel_kind_rank(e.get("kind")),
                seq if seq is not None else (1 << 30),
                str(e.get("id") or ""))

    tid, tseq = task.get("id"), task.get("seq")
    best = {}
    for direction in ("out", "in"):
        for e in (edges.get(direction) or []):
            oid, oseq = e.get("id"), e.get("seq")
            if oid:
                if oid == tid:
                    continue                    # self-edge
            elif oseq is not None and oseq == tseq:
                continue                        # id-less entry pointing at itself
            key = oid if oid else ("seq", oseq)
            cand = {"id": oid, "seq": oseq, "kind": e.get("kind"),
                    "dir": direction, "status": e.get("status")}
            prev = best.get(key)
            if prev is None or _pick(cand) < _pick(prev):
                best[key] = cand
    return sorted(best.values(), key=_order)


# --- WS-D: universal semantic edges + (gated) knowledge co-citation ----------
#
# A UNIVERSAL improvement to the task graph — derived, never stored, and computed
# entirely from signals task-station already records (PRs, stories, files, repos).
# Nothing here reads a vault or needs any config: a public user with no second brain
# gets the richer graph and unchanged everything else. The co-citation layer
# (task↔note) is the ONLY vault-aware part and is gated off by default (see
# build_board_graph's `knowledge` arg + config.knowledge_graph_enabled()).

# Signal → edge-weight for `touches-same`: a shared PR is the strongest same-work
# signal (same change), a shared story or file next, a shared repo/project the
# weakest (mere co-location). An edge's weight sums the weights of every signal the
# two tasks share, so "same PR + same files" outranks "same repo".
_SEMANTIC_WEIGHTS = {"pr": 3, "story": 2, "file": 2, "repo": 1}


def _task_signals(task):
    """The dedup set of `(kind, value)` signals used to derive `touches-same` edges:
    PR urls, story urls, recently-edited file paths, and repo/project names. Pure —
    reads only fields the store already carries; empty set on a task with none."""
    sig = set()
    for p in merged_prs(task):
        u = (p.get("url") or "").strip()
        if u:
            sig.add(("pr", u))
    for s in merged_stories(task):
        u = (s.get("url") or "").strip()
        if u:
            sig.add(("story", u))
    for f in (task.get("files") or []):
        f = (f or "").strip()
        if f:
            sig.add(("file", f))
    for r in (task.get("projects") or []):
        r = (r or "").strip()
        if r:
            sig.add(("repo", r))
    return sig


def semantic_edges(task, tasks=None):
    """Derived `touches-same` edges for one task: every OTHER task that shares a PR,
    story, file, or repo/project with it. Undirected (a symmetric relation) so each
    counterpart appears once. Each edge carries `via` (the sorted shared signal
    kinds), `weight` (sum of `_SEMANTIC_WEIGHTS` over the shared signals), and the
    other task's `status`. Sorted strongest-first. Pure derivation — nothing is
    stored on the task; a task with no signals yields []. O(N × signals)."""
    scan = tasks if tasks is not None else all_tasks()
    mine = _task_signals(task)
    if not mine:
        return []
    tid = task.get("id")
    edges = []
    for o in scan:
        if o.get("id") == tid:
            continue
        shared = mine & _task_signals(o)
        if not shared:
            continue
        via = sorted({k for k, _ in shared})
        weight = sum(_SEMANTIC_WEIGHTS.get(k, 1) for k, _ in shared)
        edges.append({"seq": o.get("seq"), "id": o.get("id"),
                      "kind": "touches-same", "via": via, "weight": weight,
                      "status": task_status(o)})
    edges.sort(key=lambda e: (-e["weight"], e.get("seq") if e.get("seq") is not None else 1 << 30))
    return edges


def _task_cited_notes(task):
    """The set of vault note slugs a task cites via `[[wikilink]]` in its human text
    (title / goal / state / summary / decisions / history). The co-citation signal
    for the second-brain-gated knowledge graph — two tasks citing the same note are
    working the same knowledge. Empty set on a task with no wikilinks (the common
    case), so this never fabricates edges.

    `knowledge.task_note_links` is the single implementation, shared with the two-plane
    view's cross-plane `cites` edge. It also excludes a `[[task:502]]` target, which is a
    reference to a TASK rather than to a note — so such a mention can no longer enter
    this signal as a note that does not exist. The measured corpus has none of them, so
    that exclusion changes no edge that exists today."""
    return _knowledge.task_note_links(task)


def build_board_graph(tasks, knowledge=False):
    """The relation graph the board mini-graph renders. Nodes = tasks; edges are:

      * lineage — the stored `related` edges (`spawned-from` directed child→parent,
        `related` undirected); UNIVERSAL.
      * `related-knowledge` — task↔task co-citation (both cite the same `[[note]]`),
        weighted by shared-note count; SECOND-BRAIN-GATED, only when `knowledge=True`.

    Derived `touches-same` (shared PR/story/file/repo) edges are NOT emitted — see the
    note at the loop below. `semantic_edges` itself is untouched and still serves the
    export and the signal hubs.

    Only nodes that touch ≥1 edge are returned, so a bare / relation-free store yields
    `{"nodes": [], "edges": []}` and the renderer omits the panel entirely (a public
    user with no relations sees exactly today's board). Lineage collapses to ONE edge
    per unordered pair, labelled by `_REL_KIND_RANK` — the same precedence
    `canonical_relations` applies to the text and board surfaces — so a pair recorded
    under two kinds draws once. The derived tiers stay deduped by unordered seq-pair
    + kind, strongest weight winning. Pure and O(N + edges) — no I/O, no config read
    (the caller resolves `knowledge`)."""
    nodes_by_seq = {}
    for t in tasks:
        seq = t.get("seq")
        if seq is None:
            continue
        cur = task_status(t)
        glyph = STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED else STATUS_GLYPH.get(cur, "○")
        nodes_by_seq[seq] = {"seq": seq, "id": t.get("id"),
                             "title": t.get("title", ""), "color": t.get("color"),
                             "status": cur, "glyph": glyph}
    by_id_seq = {t.get("id"): t.get("seq") for t in tasks}

    edges = []
    lineage = {}                   # frozenset({a,b}) → the ONE canonical lineage edge
    undirected = {}                # frozenset({a,b}) + kind → strongest edge dict

    def _add_undirected(a, b, kind, weight, via):
        if a is None or b is None or a == b:
            return
        key = (frozenset((a, b)), kind)
        prev = undirected.get(key)
        if prev is None or weight > prev["weight"]:
            undirected[key] = {"a": a, "b": b, "kind": kind, "dir": "none",
                               "weight": weight, "via": via}

    # Lineage edges from every task's stored `related` list — ONE per unordered pair.
    # A pair can be recorded up to three times (each side stores the other, and
    # `append_related` permits a second KIND for the same target), so it is collapsed
    # here on `_REL_KIND_RANK`. The winning kind decides the arrow: `spawned-from`
    # stays directed child→parent, anything else stays undirected.
    for t in tasks:
        a = t.get("seq")
        if a is None:
            continue
        for e in (t.get("related") or []):
            # Resolve the counterpart from its `id` — the only machine-portable
            # handle a stored entry carries. A `seq` is local to one store, so a
            # stored one is trusted ONLY for a legacy entry that has no id at all.
            # Preparation, not repair: today every stored entry carries both and
            # they agree, so this reorder changes nothing about the current graph.
            eid = e.get("id")
            b = by_id_seq.get(eid) if eid else e.get("seq")
            if b is None or b == a or b not in nodes_by_seq:
                continue
            kind = e.get("kind") or "related"
            key = frozenset((a, b))
            prev = lineage.get(key)
            if prev is not None:
                prev_rank, rank = _rel_kind_rank(prev["kind"]), _rel_kind_rank(kind)
                # Equal rank keeps the lexicographically smaller (a, b) so the
                # surviving orientation does not depend on the input's task order.
                if prev_rank < rank or (prev_rank == rank
                                        and (prev["a"], prev["b"]) <= (a, b)):
                    continue
            lineage[key] = {"a": a, "b": b, "kind": kind,
                            "dir": "a->b" if kind == "spawned-from" else "none",
                            "weight": 2, "via": ["lineage"]}
    edges.extend(lineage.values())

    # NO semantic `touches-same` edges. Two tasks touching one file is not a
    # relationship worth drawing, and the kind was 97% of every edge in the graph —
    # it buried the lineage it sat next to. Shared PRs / repos / stories are NOT lost:
    # they still reach the graph as signal HUBS with spokes (`build_render_graph`),
    # which say the same thing once per signal instead of once per pair. Only a
    # file-only share now draws nothing at all.
    #
    # `semantic_edges` / `_task_signals` / `_SEMANTIC_WEIGHTS` deliberately stay —
    # the vault/markdown export (`_related_pairs`) still emits its `touches same`
    # pairs, and the signal hubs still read the same signals. This is the GRAPH
    # dropping a consumer, not the signal being deleted.

    # Co-citation `related-knowledge` edges (undirected) — SECOND-BRAIN-GATED.
    if knowledge:
        cited = {}
        for t in tasks:
            seq = t.get("seq")
            if seq is None:
                continue
            notes = _task_cited_notes(t)
            if notes:
                cited[seq] = notes
        seqs = sorted(cited)
        for i in range(len(seqs)):
            for j in range(i + 1, len(seqs)):
                shared = cited[seqs[i]] & cited[seqs[j]]
                if shared:
                    _add_undirected(seqs[i], seqs[j], "related-knowledge",
                                    len(shared), sorted(shared))

    edges.extend(undirected.values())

    # Keep only nodes that participate in ≥1 edge.
    touched = set()
    for e in edges:
        touched.add(e["a"])
        touched.add(e["b"])
    nodes = [nodes_by_seq[s] for s in sorted(touched) if s in nodes_by_seq]
    # Deterministic edge order (stable render + stable tests).
    edges.sort(key=lambda e: (e["a"], e["b"], e["kind"]))
    return {"nodes": nodes, "edges": edges}


def parse_pr_number(url):
    """The PR number for a "PR <n>" label, parsed from an ADO or GitHub PR url:
    GitHub `.../pull/<n>`, ADO `.../pullrequest/<n>`, else any trailing path number.
    Returns the number as a STRING, or the trimmed url when unparseable. Pure — no I/O."""
    u = (url or "").strip()
    if not u:
        return u
    m = re.search(r"/(?:pull|pullrequest|pull-request|pullrequests)/(\d+)", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)/*$", u.rstrip("/"))
    if m:
        return m.group(1)
    return u


def shared_signal_groups(tasks):
    """Group tasks by shared pr/story/repo signal VALUE (via `_task_signals`) for the
    render graph's signal hubs. Returns `(groups, labels, singletons)`:

      * groups     — {(kind, value): [seqs]} for kind in ("pr","story","repo") shared by
                     >= 2 distinct member tasks (the hub threshold); seqs sorted.
      * labels     — {(kind, value): human label} — "PR <n>" / "repo <project>" /
                     "story <id>".
      * singletons — {seq: [labels]} for pr/story/repo signals owned by exactly ONE task
                     (no hub; surfaced later in step 3).

    Stories are keyed by their `story_ref` id (NOT the raw url) so a hub value matches the
    emitted `stories/<id>` page and a bare-id + full-url reference to the same work item
    collapse together. `file` signals are intentionally excluded — a file-only share forms
    no hub, and since the graph stopped emitting `touches-same` it now draws nothing at
    all (deliberate: "no need to link tasks on shared files"). Pure; deterministic output.
    Distinct from `_compute_story_group_ids` (different threshold + key space)."""
    try:
        import obsidian_sync
    except Exception:
        obsidian_sync = None
    members = {}        # (kind, value) -> set(seq)
    labels = {}         # (kind, value) -> human label
    for t in tasks:
        seq = t.get("seq")
        if seq is None:
            continue
        for kind, value in _task_signals(t):
            if kind == "pr":
                key = ("pr", value)
                labels[key] = "PR %s" % parse_pr_number(value)
            elif kind == "repo":
                key = ("repo", value)
                labels[key] = "repo %s" % value
            elif kind == "story":
                sid = None
                if obsidian_sync is not None:
                    try:
                        sid, _ = obsidian_sync.story_ref({"url": value})
                    except Exception:
                        sid = None
                if not sid:
                    continue
                key = ("story", sid)
                labels[key] = "story %s" % sid
            else:
                continue                       # file — no hub
            members.setdefault(key, set()).add(seq)
    groups, singletons = {}, {}
    for key, seqs in members.items():
        if len(seqs) >= 2:
            groups[key] = sorted(seqs)
        else:
            (only_seq,) = tuple(seqs)
            singletons.setdefault(only_seq, []).append(labels[key])
    for s in singletons:
        singletons[s] = sorted(singletons[s])
    return groups, labels, singletons


def build_render_graph(tasks, knowledge=False, notes=None):
    """Render-layer augmentation of `build_board_graph`: adds category + signal HUB nodes
    and re-keys every node to a typed STRING id for the board mini-graph. Pure; does NOT
    mutate `build_board_graph`'s output or the export path (both contract-pinned).

    Node ids are `"t:<seq>"` (task), `"cat:<key>"` (category hub), `"sig:<kind>:<value>"`
    (signal hub), or `"n:<slug>"` (a vault NOTE — the knowledge plane, see below). Edges
    keep `kind,dir,weight,via` and re-map `a`/`b` to those string ids.

      * Category hub — one per category with >= 2 tasks present among the base graph's task
        nodes; each member task gets a `membership` spoke. The KEY only is carried (the
        renderer resolves the hex via `_highlight_fb`).
      * Signal hub — one per shared pr/story/repo group (>= 2 members in the base graph),
        each member task getting a kind-tagged spoke. This is now the ONLY way a shared
        PR / repo / story reaches the graph, since the base stopped emitting the direct
        `touches-same` edge that used to sit alongside it.
      * lineage + `related-knowledge` edges are re-mapped direct + untouched.
      * Note node — one per note in `notes`, the KNOWLEDGE PLANE (see below).

    A task is DRAWN when it carries a base edge (lineage / co-citation), belongs to a
    >=2-member signal group, or sits on a cross-plane edge — the second clause is what
    keeps the hub tier alive now that the base no longer emits a `touches-same` edge for
    every shared signal (see the note on `present` below), and the third is what stops a
    citation pointing at a task the graph never drew.

    THE KNOWLEDGE PLANE (`notes`, from `board_notes` / `knowledge.vault_notes`). Passing
    a corpus adds a SECOND plane to this one graph: a node per note, `links-to` edges
    between notes, and the three cross-plane kinds (`cites`, `distilled-from`,
    `references`) — and nothing else crosses the gap. Every node then carries `plane`
    (`task` or `knowledge`); with no notes NO node carries it, so the default graph is
    byte-identical to the one this function returned before the plane existed — the same
    parity rule the Interbrain owner/brain stamping follows, for the same reason.

    EVERY note is drawn, edge or no edge. The corpus is global and rendered whole, so an
    orphan note is a real node that the layout seats out at the rim; that is a fact about
    the vault, not an absence to hide.

    Every node carries `deg` (incident-edge count). Nodes are sorted by (type-rank,
    seq/key/value/slug) and edges by (kind, a, b) — type-homogeneous keys only, never
    mixing int/str. Returns `{nodes, edges, singletons}` where `singletons` =
    `{seq: [labels]}` for pr/story/repo signals with exactly one task (renderer reads
    only nodes/edges). A relation-free / solo board with no notes (no base edges AND no
    shared signal AND no corpus) returns the base unchanged so the panel stays absent."""
    base = build_board_graph(tasks, knowledge)
    tasks_by_seq = {t.get("seq"): t for t in tasks if t.get("seq") is not None}
    notes = list(notes or [])

    # WHICH TASKS THE GRAPH DRAWS. Until the base stopped emitting `touches-same`,
    # every task sharing a PR/repo/story with another was ALREADY a base node — that
    # edge put it there — so "base nodes" and "base nodes + signal-group members" were
    # the same set and this distinction never showed. They are not the same set now:
    # sourcing `present` from base nodes alone would delete every signal HUB along with
    # the direct edge, since a hub is only built from tasks already present. Unioning
    # the >=2-member signal groups back in keeps the hub tier exactly as it renders
    # today, so the ONLY thing the `touches-same` removal actually drops is a
    # file-only share — which is precisely the ruling ("no need to link tasks on
    # shared files"); a file signal forms no hub, so it now draws nothing at all.
    groups, labels, singletons = shared_signal_groups(tasks)
    grouped = set()
    for _gseqs in groups.values():
        if len(_gseqs) >= 2:
            grouped.update(_gseqs)
    # The cross-plane edges are resolved HERE because `present` needs them: a task whose
    # only relation is a citation of a note has no base edge and no signal group, so
    # without this clause the citation would point at a task node that was never built.
    cross = _knowledge.cross_plane_edges(tasks, notes) if notes else []
    linked = {e["seq"] for e in cross if e.get("seq") in tasks_by_seq}
    if not base["edges"] and not grouped and not notes:
        return base                                 # relation-free / solo → no panel

    base_seqs = {n["seq"] for n in base["nodes"]}
    present = base_seqs | grouped | linked          # drawn task seqs (int)

    def tid(seq):
        return "t:%d" % seq

    def nid(slug):
        return "n:%s" % slug

    # ---- task nodes (fresh dicts — never mutate base) --------------------------
    nodes = []
    for n in base["nodes"]:
        nodes.append({"id": tid(n["seq"]), "type": "task", "seq": n["seq"],
                      "title": n.get("title", ""), "color": n.get("color"),
                      "status": n.get("status"), "glyph": n.get("glyph")})
    # A task that reaches the graph ONLY through a signal hub has no base node, so its
    # node dict is built here — same fields, same glyph rule as build_board_graph.
    for seq in sorted(present - base_seqs):
        t = tasks_by_seq.get(seq)
        if t is None:
            continue
        cur = task_status(t)
        nodes.append({"id": tid(seq), "type": "task", "seq": seq,
                      "title": t.get("title", ""), "color": t.get("color"),
                      "status": cur,
                      "glyph": (STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED
                                else STATUS_GLYPH.get(cur, "○"))})

    edges = []

    # ---- category hubs: one per category with >= 1 present task ----------------
    # (Threshold is >=1 so every category present in the edge graph gets a hub; the
    # relation-free/solo board is still gated out by the early return above. The hub's
    # `key` is the NORMALISED category, while a task node carries its stored `color`
    # raw — the board's filter keys on the latter and looks hubs up by the former, so
    # the two must stay the same string. They do: every write path normalises `color`.)
    try:
        import categories
    except Exception:
        categories = None
    if categories is not None:
        cat_members = {}                              # key -> [seqs present]
        for seq in present:
            t = tasks_by_seq.get(seq)
            if t is None:
                continue
            try:
                ckey = categories.normalize(t.get("color") or "")
            except Exception:
                continue
            cat_members.setdefault(ckey, []).append(seq)
        for ckey in sorted(cat_members):
            seqs = sorted(cat_members[ckey])
            if len(seqs) < 1:
                continue
            try:
                meta = categories.hub_meta(ckey)
                label = "%s [%s] %s" % (meta["dot"], meta["tag"], meta["label"])
            except Exception:
                label = ckey
            nodes.append({"id": "cat:%s" % ckey, "type": "hub", "key": ckey,
                          "label": label, "status": "open"})
            for seq in seqs:
                edges.append({"a": tid(seq), "b": "cat:%s" % ckey,
                              "kind": "membership", "dir": "none", "weight": 1,
                              "via": []})

    # ---- signal hubs: shared pr/story/repo groups (>= 2 present members) -------
    # `groups`/`labels`/`singletons` were resolved above, where `present` needed them.
    sig_by_seq = {}                                   # seq -> set(signal hub id)
    for key in sorted(groups):
        kind, value = key
        seqs = [s for s in groups[key] if s in present]
        if len(seqs) < 2:
            continue
        sid = "sig:%s:%s" % (kind, value)
        nodes.append({"id": sid, "type": "signal", "kind": kind,
                      "label": labels[key]})
        w = _SEMANTIC_WEIGHTS.get(kind, 1)
        for seq in seqs:
            edges.append({"a": tid(seq), "b": sid, "kind": kind, "dir": "none",
                          "weight": w, "via": [labels[key]]})
            sig_by_seq.setdefault(seq, set()).add(sid)

    # ---- re-map base edges -----------------------------------------------------
    # The `touches-same` branch is now UNREACHABLE from build_board_graph, which no
    # longer emits that kind. Kept as a guard so a hand-built or future base graph
    # carrying one still can't draw an edge that duplicates a signal hub; delete it
    # (and `sig_by_seq`) if the kind is ever removed from the codebase entirely.
    for e in base["edges"]:
        if e["kind"] == "touches-same":
            shared_hub = sig_by_seq.get(e["a"], set()) & sig_by_seq.get(e["b"], set())
            if shared_hub:
                continue                              # redundant with a signal hub
        edges.append({"a": tid(e["a"]), "b": tid(e["b"]), "kind": e["kind"],
                      "dir": e.get("dir", "none"), "weight": e.get("weight", 1),
                      "via": list(e.get("via") or [])})

    # ---- the KNOWLEDGE PLANE: one node per note, then its own edges -------------
    # A note node carries the frontmatter under `kind` (its `type`) and `area`, because
    # `type` on a node already means the structural class (task / hub / signal / note)
    # and one word cannot mean both. `area` is left exactly as the vault wrote it —
    # empty when the note declares none — and the LAYOUT is what maps that to the
    # `unfiled` sector, since which sector a note sits in is a placement decision.
    note_slugs = set()
    for n in notes:
        slug = n.get("slug")
        if not slug or slug in note_slugs:
            continue
        note_slugs.add(slug)
        nodes.append({"id": nid(slug), "type": "note", "slug": slug,
                      "title": n.get("title") or slug,
                      "description": n.get("description", ""),
                      "kind": n.get("type", ""), "area": n.get("area", ""),
                      "path": n.get("path", "")})
    for e in _knowledge.note_edges(notes):
        edges.append({"a": nid(e["a"]), "b": nid(e["b"]), "kind": e["kind"],
                      "dir": e.get("dir", "none"), "weight": e.get("weight", 1),
                      "via": []})
    # THE GAP. Exactly three kinds cross it, and the direction asymmetry is deliberate:
    # `cites` is a task pointing UP at what it read, `distilled-from` a note pointing
    # DOWN at what produced it. They are not inverses, so both are drawn when both hold.
    # An endpoint that does not exist was already dropped by `cross_plane_edges`; the
    # `present`/`note_slugs` checks here are the second half of the same rule, for a
    # task the drawn set still excludes.
    for e in cross:
        seq, slug = e.get("seq"), e.get("slug")
        if seq not in present or slug not in note_slugs:
            continue
        if e["dir"] == "task->note":
            a, b = tid(seq), nid(slug)
        else:
            a, b = nid(slug), tid(seq)
        edges.append({"a": a, "b": b, "kind": e["kind"], "dir": "a->b",
                      "weight": 1, "via": []})

    # ---- node degree -----------------------------------------------------------
    deg = {}
    for e in edges:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    for n in nodes:
        n["deg"] = deg.get(n["id"], 0)

    # WHICH PLANE EACH NODE IS ON — stamped only when a corpus was passed, so a board
    # with no notes emits the identical node dicts it emitted before this plane existed.
    # A category or signal hub is task-plane furniture, so it takes `task` too.
    if notes:
        for n in nodes:
            n["plane"] = "knowledge" if n.get("type") == "note" else "task"

    # ---- deterministic order (type-homogeneous sort keys only) -----------------
    def _node_key(n):
        t = n.get("type")
        if t == "task":
            return (0, n.get("seq"))
        if t == "hub":
            return (1, n.get("key") or "")
        if t == "note":
            return (3, n.get("slug") or "")
        return (2, n.get("kind") or "", n.get("id") or "")
    nodes.sort(key=_node_key)
    edges.sort(key=lambda e: (e["kind"], e["a"], e["b"]))
    return {"nodes": nodes, "edges": edges, "singletons": singletons}


def _worker_name(w):
    """`project` or `project:label` for a worker row in the Sessions tree."""
    proj = w.get("project") or "?"
    lbl = w.get("label")
    return "%s:%s" % (proj, lbl) if lbl else proj


def _session_block_lines(task):
    """The terminal-detail `Sessions:` block (hubs with workers nested under their
    spawning hub), using the canonical session-state vocabulary: ● running (a Claude
    process is alive now) / ◐ resumable (saved transcript, nothing running) / ○ gone
    (no transcript found). Returns [] when the task has no recorded sessions — so a
    bare task stays exactly as it renders today."""
    if not task.get("session_meta"):
        return []
    tree = session_tree(task)
    hubs = tree.get("hubs") or []
    orphans = tree.get("orphan_workers") or []
    if not hubs and not orphans:
        return []
    running_sids = set(_live_session_index())
    HUB_CAP, WK_CAP = 6, 4
    out = ["Sessions:"]

    def _wk_line(w, indent):
        return "%s↳ %s  worker (%s) — %s · %s" % (
            indent, (w.get("sid") or "????????")[:8], _worker_name(w),
            w.get("model") or "?", rel_time(w.get("ts")))

    for h in hubs[:HUB_CAP]:
        tags = ["hub", "main" if h.get("main") else "side-quest"]
        if h.get("pinned"):
            tags.append("pinned")
        if h.get("sid") in running_sids:
            glyph, word = "●", "running"
        elif h.get("live"):
            glyph, word = "◐", "resumable"
        else:
            glyph, word = "○", "gone"
        ordn = h.get("ordinal")
        onum = ("%s-%s " % (task.get("seq"), ordn)
                if ordn is not None and task.get("seq") is not None else "")
        out.append("  %s %s%s  %s — %s · %d msgs · %s · %s" % (
            glyph, onum, (h.get("sid") or "????????")[:8],
            " · ".join(tags), word,
            h.get("msgs") or 0, rel_time(h.get("ts")), _tildify(h.get("cwd"))))
        wks = h.get("workers") or []
        for w in wks[:WK_CAP]:
            out.append(_wk_line(w, "      "))
        if len(wks) > WK_CAP:
            out.append("      … +%d more worker(s)" % (len(wks) - WK_CAP))
    if len(hubs) > HUB_CAP:
        out.append("  … +%d more hub(s)" % (len(hubs) - HUB_CAP))
    for w in orphans[:WK_CAP]:
        out.append("  ↳ (unlinked) %s  worker (%s) — %s · %s" % (
            (w.get("sid") or "????????")[:8], _worker_name(w),
            w.get("model") or "?", rel_time(w.get("ts"))))
    if len(orphans) > WK_CAP:
        out.append("  … +%d more worker(s)" % (len(orphans) - WK_CAP))
    return out


# How each relation kind READS on the `Related:` line, as (stored-here, derived-here).
# The second word is the inverse a reader sees from the OTHER end: only the subordinate
# side ever stores an edge, so every superior side is a derived reading. Kinds absent
# from this table fall back to `related #N` both ways, which is what keeps a store
# written by a newer version renderable.
_REL_LINE_WORDS = {
    "depends-on":   ("depends on #%s", "blocks #%s"),
    "parent":       ("parent #%s",     "children #%s"),
    "duplicates":   ("duplicates #%s", "duplicates #%s"),   # symmetric — reads the same
    "replaces":     ("replaces #%s",   "replaced by #%s"),
    "absorbed-by":  ("absorbed-by #%s", "absorbed #%s"),
    "spawned-from": ("from #%s (spawned-from)", "spawned #%s"),
}
_REL_LINE_DEFAULT = ("related #%s", "related #%s")

# The same words as bare LABELS, for the grouped form. A run of one still renders through
# the format strings above, so a lone `spawned-from` keeps its ` (spawned-from)` qualifier
# byte-for-byte; only a repeat collapses to `label #a, #b`.
_REL_LINE_LABELS = {
    "depends-on":   ("depends on", "blocks"),
    "parent":       ("parent", "children"),
    "duplicates":   ("duplicates", "duplicates"),
    "replaces":     ("replaces", "replaced by"),
    "absorbed-by":  ("absorbed-by", "absorbed"),
    "spawned-from": ("from", "spawned"),
}
_REL_LINE_LABELS_DEFAULT = ("related", "related")


def _related_line(task, edges=None):
    """The `Related:` artifacts line, or None when the task has no relation edges
    (either direction). ONE entry per counterpart — a reciprocal pair is a single
    relationship, so it prints once, resolved through `canonical_relations`.

    Each kind renders as its stored word or its derived inverse (`_REL_LINE_WORDS`):
    `depends on #N` / `blocks #N` · `parent #P` / `children #Q` · `duplicates #N` ·
    `replaces #N` / `replaced by #N` · `absorbed-by #N` / `absorbed #N` ·
    `from #N (spawned-from)` / `spawned #N`, with anything else — including `related`
    and any future kind — reading `related #N`. A closed edge target gets a trailing
    ` ✕`.

    CONSECUTIVE entries sharing a label are GROUPED under it — `children #Q, #R` — so a
    parent with eight children reads the label once rather than eight times.
    `canonical_relations` sorts by kind, so same-kind entries are already adjacent. The
    closed mark is per TARGET, so it rides with its own `#N` and grouping never loses it;
    a run of one renders through the format strings above, unchanged."""
    runs = []
    for r in canonical_relations(task, edges=edges):
        mark = " ✕" if r.get("status") == STATUS_CLOSED else ""
        out = r.get("dir") == "out"
        idx = 0 if out else 1
        label = _REL_LINE_LABELS.get(r.get("kind"), _REL_LINE_LABELS_DEFAULT)[idx]
        fmt = _REL_LINE_WORDS.get(r.get("kind"), _REL_LINE_DEFAULT)[idx]
        if runs and runs[-1][0] == label:
            runs[-1][1].append("#%s%s" % (r.get("seq"), mark))
        else:
            runs.append([label, ["#%s%s" % (r.get("seq"), mark)], (fmt % r.get("seq")) + mark])
    parts = [run[2] if len(run[1]) == 1 else "%s %s" % (run[0], ", ".join(run[1]))
             for run in runs]
    return ("Related: " + " · ".join(parts)) if parts else None


def _session_resume_command(sid, meta_entry):
    """The `cd <cwd> && claude …` resume one-liner for ONE recorded session id (used
    by the per-hub-session board rows), or None when no cwd is knowable. Mirrors
    `_resume_target`'s self-correcting-cwd logic for a single sid: a live transcript
    (≥1 user msg) → `--resume <sid>` with the transcript's own launch cwd; a preborn
    unborn session → `--session-id <sid>`; else a plain fresh `claude` in the recorded
    cwd. Pure display — mints nothing."""
    meta_entry = meta_entry or {}
    path = _find_session_path(sid)
    if path and _session_msgcount(path) >= 1:
        cwd = _session_cwd(path) or meta_entry.get("cwd")
        if cwd:
            return bg_aware_resume(sid, cwd)
    cwd = meta_entry.get("cwd")
    if not cwd:
        return None
    if meta_entry.get("preborn"):
        return "cd %s && claude --session-id %s" % (cwd, sid)
    return "cd %s && claude" % cwd


def _hub_sessions(task, live_sids=None):
    """Per-hub-session one-liners for the board's merged Sessions section (WS7 → WS6).

    One dict per `session_meta` hub, pinned-first then newest-first:
      {sid8, session_id, oneliner, pinned, live, role, msgs, age, cost_usd,
       out_tokens, resume_command}
    `oneliner` is that session's first captured user PROMPT (truncated to 80 chars;
    command/compaction rows skipped) else a plain attach note; `cost_usd`/`out_tokens`
    come from the usage ledger row for the sid (`cost_usd` None when it used an unknown
    model). Reuses WS3's `session_tree` hub set when that sibling is merged, else derives
    hubs inline from `session_meta`. [] when the task has no `session_meta` — so a bare
    task adds nothing. Defensive: any ledger hiccup degrades to no cost/one-liner, never
    breaks the board."""
    meta = task.get("session_meta") or {}
    if not meta:
        return []
    live = set(live_sids or [])
    pinned_sid = task.get("pinned_session")
    ledger = {}
    first_prompt = {}
    try:
        import config as _cfg
        if _cfg.usage_tracking_enabled():
            store = _backend()
            for r in store.session_usage_for_task(task["id"]):
                sid = r.get("session_id")
                if sid:
                    ledger[sid] = r
            for p in _usage_engine().task_prompts(store, task):
                sid = p.get("session_id")
                if sid and p.get("kind") == "prompt" and sid not in first_prompt:
                    first_prompt[sid] = (p.get("text") or "").strip()
    except Exception:
        pass
    hubs = [(sid, (m or {})) for sid, m in meta.items() if (m or {}).get("role") == "hub"]
    # pinned first, then newest recorded meta ts first.
    hubs.sort(key=lambda kv: (kv[0] == pinned_sid, kv[1].get("ts") or 0), reverse=True)
    out = []
    for sid, m in hubs:
        path = _find_session_path(sid)
        msgs = _session_msgcount(path) if path else 0
        one = first_prompt.get(sid) or ("attached · %s" % _tildify(m.get("cwd")))
        row = ledger.get(sid)
        cost_usd, out_tok = None, 0
        if row is not None:
            mm = _merge_row_models(row)
            out_tok = sum(d.get("out") or 0 for d in mm.values())
            costs = [d.get("cost_usd") for d in mm.values()]
            cost_usd = None if any(c is None for c in costs) else round(sum(costs), 6)
        out.append({
            "sid8": sid[:8],
            "session_id": sid,
            "oneliner": one[:80],
            "pinned": sid == pinned_sid,
            "live": sid in live,
            "role": "hub",
            "ordinal": m.get("ordinal"),
            "msgs": msgs,
            "age": rel_time(m.get("ts")),
            "cost_usd": cost_usd,
            "out_tokens": out_tok,
            "resume_command": _session_resume_command(sid, m),
        })
    return out


def _live_session_index():
    """`{session_id: row}` for every ACTUALLY-running Claude session (from
    live_sessions.running()), or `{}` if the viewer can't be loaded/read. Guarded so
    a sessions-dir hiccup never breaks the /todo detail or board render."""
    try:
        import live_sessions
        return {r["session_id"]: r for r in live_sessions.running() if r.get("session_id")}
    except Exception:
        return {}


def _live_note(row):
    """The `  ● busy · 1m ago` annotation appended to a resume/worker line whose
    session is live, or '' when the row is None (session not running). The ● marks
    it as a real process; the busy/idle + age come straight from the sessions file."""
    if not row:
        return ""
    return "  ● %s · %s" % (row.get("status") or "live", rel_time(row.get("updated_ts")))


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


# ------------------------------------------------------------- subcommands ----

def _is_substantive_tracked(session):
    """True when `session` is itself a real, tracked working conversation — linked
    to a live task (not unlinked, not skipped) AND past the substance floor. Used
    by `create` to avoid binding a busy parent conversation as a NEW task's resume
    target (the spun-off-task tainting bug)."""
    if not session:
        return False
    link = get_link(session)
    if not link or link == SKIP_SENTINEL:
        return False
    path = _find_session_path(session)
    return bool(path) and _session_msgcount(path) >= SUBSTANCE_FLOOR


def cmd_create(a):
    if not getattr(a, "force", False):
        dup = similar_open_task(a.title)
        if dup:
            attach_hint = ("attach --session %s --task %s" % (a.session, dup["id"][:8])
                           if getattr(a, "session", None)
                           else "attach --session <session-id> --task %s" % dup["id"][:8])
            print("Not created — likely a duplicate of open task [%s] %s.\n"
                  "Attach instead:  task-station %s\n"
                  "Or re-run create with --force to make a separate task."
                  % (dup["id"][:8], dup["title"], attach_hint))
            return
    requested = getattr(a, "color", None)
    if cats and requested and not cats.is_known(requested):
        print("⚠ --color '%s' is not a known category; defaulting to %s. "
              "Recategorize later with: attach --color <key|emoji|[TAG]>."
              % (requested, cats.DEFAULT))
    if getattr(a, "effort", None) and not normalize_effort(a.effort):
        print("⚠ --effort '%s' is not a known size; leaving it unset. "
              "Use xs/s/m/l/xl (or 1–5)." % a.effort)
    status = STATUS_ACTIVE if getattr(a, "active", False) else STATUS_DEFAULT
    task = new_task(a.title, a.summary, requested, getattr(a, "effort", None), status=status)
    # Structured-digest seeds: a one-line `goal` and an initial `--step` checklist
    # (repeatable). Stored straight on the task blob (no schema migration).
    goal = getattr(a, "goal", None)
    if goal:
        task["goal"] = goal.strip()
        # Baseline it here too, not only on `update --goal`. A goal written at creation
        # has an EXACTLY knowable baseline — this moment, zero decisions — and leaving it
        # unstamped would make heal's goal review say "cannot be counted" for the whole
        # life of a task whose goal was never rewritten. Uncountable is the honest answer
        # when nobody recorded the baseline; it is the wrong answer when we are standing
        # at the baseline.
        _heal.stamp_goal_touched(task)
    for s in (getattr(a, "step", None) or []):
        append_step(task, s)
    create_with_seq(task)              # atomically mint the stable number + persist
    # F4 auto-attach: score the new task against every brain and (silently) file it in
    # the winning brain. The user never names one; 'main' when nothing scores. Fail-open
    # — no brains config / a single-brain user just stays on 'main' (zero-impact law).
    auto_attach_brain(task, getattr(a, "session", None))

    session = getattr(a, "session", None)
    no_attach = getattr(a, "no_attach", False)
    # #6: creating from a SUBSTANTIVE tracked conversation defaults to no-attach so
    # the busy parent session isn't silently made the new task's resume target.
    # `--attach` forces the old bind-this-session behaviour; `--no-attach` is explicit.
    substantive = (not no_attach and not getattr(a, "attach", False)
                   and _is_substantive_tracked(session))
    spawn_parent = None
    if substantive:
        no_attach = True
        # The creating conversation is itself a real, tracked task — this new task
        # was spun off from it (the 363→365 silent-spawn case). Record a spawned-from
        # edge on the child and let the parent's event feed hear about the spin-off.
        spawn_parent = load_task(get_link(session))
        if spawn_parent:
            append_related(task, spawn_parent, "spawned-from")
            add_event(spawn_parent, "child",
                      "spawned #%s: %s" % (task["seq"], task["title"]), session)
            save_task(spawn_parent)

    if no_attach or not session:
        # Unattached create: empty sessions[]/session_meta, no session→task link.
        # `/todo <n> -s` then has no recorded session and fresh-starts a clean one.
        touch(task, note="created (no-attach)")
        save_task(task)
        _obsidian_sync(task)
        _stream_emit("task.created", task, _stream_created_data(task), session)
        if substantive:
            print("⚠ Created from a substantive tracked session — NOT binding this "
                  "conversation as the new task's resume target (use --attach to "
                  "override). /todo %s -s starts a fresh session." % task["seq"])
            if spawn_parent:
                print("   ↳ spawned-from #%s" % spawn_parent.get("seq"))
        else:
            print("📋 Created task [%s] %s (unattached). /todo %s -s starts a fresh "
                  "session." % (task["id"][:8], task["title"], task["seq"]))
        for line in cat_lines(task.get("color")):
            print(line)
        auto_enable_category(task.get("color"))
        return

    touch(task, session=session, note="created")
    save_task(task)
    _obsidian_sync(task)
    _stream_emit("task.created", task, _stream_created_data(task), session)
    set_link(session, task["id"])
    clear_count(session)
    print("📋 Created and attached to task [%s] %s" % (task["id"][:8], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW, not on the next prompt
    _emit_title_to_origin(task)                # label the window NOW, not next prompt


def cmd_attach(a):
    task = resolve_ref(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    # F9 identity soft-guard: if the attaching prompt/--note names a PR/work-item
    # and the target task carries DIFFERENT identity keys (both sides keyed, empty
    # intersection), this is almost certainly a fold-into-the-wrong-task — warn and
    # EXIT NONZERO without attaching unless --force-key confirms. Keyless on either
    # side ⇒ proceeds exactly as before (zero behavior change for keyless flows).
    if not getattr(a, "force_key", False):
        pkeys = extract_identity_keys(getattr(a, "note", None) or "")
        tkeys = task_identity_keys(task)
        if pkeys and tkeys and not (pkeys & tkeys):
            print("⚠ key mismatch: prompt has %s, task #%s carries %s — attach "
                  "anyway? re-run with --force-key to confirm."
                  % (render_identity_keys(pkeys),
                     task.get("seq") or task["id"][:8], render_identity_keys(tkeys)))
            sys.exit(1)
    reopened = task.get("status") == "closed"
    # When categories are on: a recognized --color (re)categorizes the task —
    # this is how a task auto-tracked as the default 'general' gets corrected to
    # its real topic later. An unrecognized --color is REFUSED, not silently
    # mapped to the default, so a typo / stray emoji can't quietly mislabel the
    # task. With no --color we only backfill the default on a task that has none.
    if cats:
        requested = getattr(a, "color", None)
        if requested and cats.is_known(requested):
            task["color"] = cats.normalize(requested)
        elif requested:
            print("⚠ Ignoring --color '%s': not a known category. Use a key, "
                  "emoji, or [TAG] — e.g. brown, 🟤, or DATA. (Keeping %s.)"
                  % (requested, task.get("color") or cats.DEFAULT))
            if not task.get("color"):
                task["color"] = cats.DEFAULT
        elif not task.get("color"):
            task["color"] = cats.DEFAULT
    touch(task, session=a.session, note="attached", reopen=True)
    # --note folds a cross-session prompt into this task's activity log instead of
    # spawning a sibling task ("fold don't fork" — see commands/todo.md §grouping).
    note = getattr(a, "note", None)
    if note and note.strip():
        add_log(task, note.strip())
        clear_provisional(task)   # a folded-in note is genuine engagement
    save_task(task)
    # F4 auto-attach re-score: attach-with-edit is the promote-to-active moment — the
    # category/summary may have just been corrected. Re-score, but auto_assign only moves
    # a task still on 'main' (never yanks it out of a scored/pinned brain silently).
    auto_attach_brain(task, a.session)
    set_link(a.session, task["id"])
    clear_count(a.session)
    if reopened:
        maybe_refresh_board()   # a reopened task flips closed → open on the board
    print("📋 Attached to task [%s] %s%s%s"
          % (task["id"][:8], task["title"], " (reopened)" if reopened else "",
             " (note appended)" if note and note.strip() else ""))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW on attach/recategorize
    _emit_title_to_origin(task)                # relabel the window NOW on attach


def cmd_bump(a):
    task_id = get_link(a.session)
    if not task_id:
        return
    task = load_task(task_id)
    if not task:
        return
    touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
    save_task(task)


def cmd_skip(a):
    # GC: if this session is attached to a still-PROVISIONAL auto-task (created by
    # guaranteed-tracking and never engaged), skipping means it was throwaway —
    # delete it so the board carries no litter.
    gc_note = ""
    link = get_link(a.session)
    if link and link != SKIP_SENTINEL:
        task = load_task(link)
        if task and task.get("provisional"):
            delete_task(task["id"])
            gc_note = (" Removed the untouched provisional task [%s] %s."
                       % (task["id"][:8], task["title"]))
    set_link(a.session, SKIP_SENTINEL)
    clear_count(a.session)
    clear_edit_markers(a.session)   # skip is a deliberate opt-out — stop the gate nagging
    print("This session is marked untracked — the [task-station] nudge will stay silent. "
          "Attaching to or creating a task later resumes tracking.%s" % gc_note)


def cmd_detach(a):
    """Remove a session from a task's resume candidates.

    Drops `<session>` from the task's `sessions[]` and `session_meta`, clears
    `pinned_session` if it pointed at this session, and clears the session→task
    link if it still points here. `--task` selects the task; without it, the
    session's currently-linked task is used. Idempotent — a missing reference just
    reports "nothing to detach"."""
    session = a.session
    task = (resolve_ref(a.task) or load_task(a.task)) if getattr(a, "task", None) else None
    if not task:
        link = get_link(session)
        if link and link != SKIP_SENTINEL:
            task = load_task(link)
    if not task:
        print("detach: no task for session %s — pass --task <id-or-number>." % session[:8])
        return
    label = task.get("seq", task["id"][:8])
    cleared = []
    if session in task.get("sessions", []):
        task["sessions"].remove(session)
        cleared.append("sessions[]")
    meta = task.get("session_meta") or {}
    if session in meta:
        del meta[session]
        cleared.append("session_meta")
    if task.get("pinned_session") == session:
        task.pop("pinned_session", None)
        cleared.append("pin")
    if not cleared:
        print("Session %s was not attached to task %s — nothing to detach."
              % (session[:8], label))
        return
    touch(task, note="detached session %s" % session[:8])
    save_task(task)
    if get_link(session) == task["id"]:
        clear_link(session)
        clear_count(session)
        cleared.append("link")
    print("Detached session %s from task %s (cleared: %s)."
          % (session[:8], label, ", ".join(cleared)))


def _open_tasks_brief(limit=8):
    """A compact 'tasks on the board you might attach to' list for hook reasons."""
    rows = [t for t in sorted_tasks() if is_on_board(t)][:limit]
    return "\n".join("  - #%s [%s] %s" % (t.get("seq") or "?", t["id"][:8], t["title"]) for t in rows)


def cmd_mark_edited(a):
    """PostToolUse(Write|Edit|NotebookEdit): if this session edited a file but is
    NOT tracking a task, emit a one-shot reminder. Silent when already tracked,
    skipped, or already reminded — so it costs ~one injection per session, max."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    link = get_link(a.session)
    if link == SKIP_SENTINEL:      # session deliberately untracked — stay silent
        return
    if link:                       # attached to a real task — editing means work
        # has started, so promote an open task to active (idempotent), then
        # we're done (tracked sessions get no nudge). Editing is genuine
        # engagement, so an auto-tracked task is no longer provisional.
        if load_task(link):
            # Concurrent-safe: parallel edit hooks on the same task each land their
            # digest tally + promotion without clobbering the others.
            def _apply(task):
                promote_active(task)
                if task.get("provisional"):
                    clear_provisional(task)
                # A real file edit is substantive work → the digest is now stale
                # (marks even an already-active task, where promote_active is a no-op)
                # and counts as one event toward the milestone staleness nudge.
                mark_digest_dirty(task)
                bump_digest_events(task)   # always advances the milestone tally
            mutate(link, _apply)
        return
    if not mark_edited(a.session):  # one-shot: the reminder already fired
        return
    msg = (
        "[task-station] You just edited a file and this session is NOT tracking a task. "
        "This is exactly the work that should be tracked. Attach to an existing "
        "task or create one NOW (or `skip` if this is genuinely throwaway) — the "
        "Stop gate will otherwise refuse to end the turn until you do.\n"
        "Create:  task-station create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "Attach:  task-station attach --session %s --task <id-or-number>\n"
        "%s\n"
        "Open tasks:\n%s"
        % (a.session, a.session, _cli_fallback(), _open_tasks_brief() or "  (none)")
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}}))


def cmd_touch_file(a):
    """PostToolUse(Write|Edit|NotebookEdit): append the edited file path to the
    attached task's `files` briefing list (deduped, capped, most-recent-last).

    Cheap + best-effort: a silent no-op when the session has no attached task (or
    is skipped) or no path was passed. No log entry, no status change, no reminder
    — that's mark-edited's job; this only enriches the briefing."""
    path = getattr(a, "file", None)
    if not path:
        return
    link = get_link(a.session)
    if not link or link == SKIP_SENTINEL:
        return
    task = load_task(link)
    if not task:
        return
    if append_edited_file(task, path):
        save_task(task)


def cmd_stop_gate(a):
    """Stop hook: refuse to end the turn if this session edited files but never
    tracked a task. Self-healing — clears its markers the moment a task is
    attached or the session is skipped — and capped at STOP_GATE_MAX_BLOCKS so a
    non-complying loop can't wedge the session."""
    if os.environ.get("TASK_STATION_GATE") == "off":
        return
    if not has_edited(a.session):
        return                              # no untracked edits → nothing to enforce
    link = get_link(a.session)
    if link:                                # real task attached, or skipped
        clear_edit_markers(a.session)
        return
    if get_blocked(a.session) >= STOP_GATE_MAX_BLOCKS:
        clear_edit_markers(a.session)       # gave it two tries — don't wedge the session
        return
    bump_blocked(a.session)
    reason = (
        "This session edited files but is not tracking a /todo task. Before you "
        "finish, attach to an existing task or create one — or mark the session "
        "skipped if this edit is genuinely throwaway. Pick exactly one:\n"
        "  Create:  task-station create --session %s --color <color> "
        "--effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'\n"
        "  Attach:  task-station attach --session %s --task <id-or-number>\n"
        "  Skip:    task-station skip --session %s\n"
        "%s\n"
        "Open tasks:\n%s"
        % (a.session, a.session, a.session, _cli_fallback(),
           _open_tasks_brief() or "  (none)")
    )
    print(json.dumps({"decision": "block", "reason": reason}))


def cmd_post_compact(a):
    """PostCompact hook (opt-in auto-checkpoint): durably stash the harness's
    compaction summary into the attached task's history — ZERO model tokens,
    retrievable via `/todo <n> history`.

    Reads the compaction summary from stdin (the hook pipes it), trims + single-
    space-collapses it to ~1200 chars, and appends a dated `history` entry. Does
    NOT touch summary/state — this is a durable backup record, not the structured
    digest. Best-effort and silent: a no-op when auto-checkpoint is off, no task is
    attached, or the session is skipped."""
    if not _auto_checkpoint_enabled():
        return
    task = _session_task(a.session)
    if not task:
        return                                  # unattached / skipped → nothing to stash
    summary = ""
    try:
        if not sys.stdin.isatty():
            summary = sys.stdin.read()
    except Exception:
        summary = ""
    summary = " ".join((summary or "").split())  # collapse newlines/runs of whitespace
    if len(summary) > 1200:
        summary = summary[:1200].rstrip() + "…"
    trigger = (getattr(a, "trigger", "") or "").strip() or "unknown"
    text = ("context compacted (%s): %s" % (trigger, summary)) if summary \
           else ("context compacted (%s)" % trigger)

    def _apply(t):                                # stdin already consumed above, so the
        if append_history(t, text, session=a.session):   # mutator stays pure/retryable
            touch(t, session=a.session,
                  note="compaction summary stashed to history (%s)" % trigger)
    mutate(task["id"], _apply)


def cmd_stop_nudge(a):
    """Stop hook (opt-in auto-checkpoint): print at most ONE non-blocking Stop
    additionalContext line, with precedence:

    1. PROACTIVE context-pressure nudge — asks the model to run a FULL structured
       `/todo save` NOW, from full context, BEFORE the harness auto-compacts. It fires
       when EITHER trigger crosses:
         • checkpoint_pct (the DEFAULT): the MEASURED context (measure_context_tokens,
           read from the transcript's real usage block) reaches checkpoint_pct% of
           context_window — the accurate, window-relative signal.
         • checkpoint_at (LEGACY/fallback): the transcript-size token ESTIMATE grows
           past an explicitly-set absolute threshold — the back-compat path, used when a
           real measurement isn't available.
       Fires ONCE per pressure episode: `pressure_nudged` is set when emitted and held
       until a `/todo save` clears it, so an ignored nudge is NOT re-spammed every turn.
    2. LIGHT staleness nudge — only when the pressure trigger did NOT fire and the digest
       is stale. Activity-gated by checkpoint_milestone_edits: it holds until N meaningful
       events (edits / promotions) have accrued since the last refresh (default 5), so a
       couple of small edits no longer nudge; 0/off restores nudge-on-any-staleness.

    Never emits both in one Stop. Prints nothing unless auto-checkpoint is ON and a task
    is attached — so it never fires on today's default setup. Deliberately NOT a block
    (no decision:block) — avoids the Stop gate's block cap / hard interrupts. Best-effort:
    the Stop hook emits whatever this prints."""
    if not _auto_checkpoint_enabled():
        return
    task = _session_task(a.session)
    if not task:
        return
    try:
        import config
        pct = config.checkpoint_pct()
        # Size the window to the model actually in use (Opus-1M → 1M, Haiku/Sonnet →
        # 200k) unless the user has explicitly set context_window. A fixed 200k
        # denominator on a 1M model reads ~5x over-full and fires this nudge almost
        # every Stop — the "saves too often / percentages look reversed" bug.
        window = effective_context_window(a.session)
        thresh_abs = config.checkpoint_at()
        milestone = config.checkpoint_milestone_edits()
    except Exception:
        pct, window, thresh_abs, milestone = 0, 200000, 0, 0
    # 1. Proactive context-pressure trigger (takes precedence over the staleness nudge).
    #    checkpoint_pct (measured) is the default path; checkpoint_at (estimated) is the
    #    absolute back-compat fallback. Either crossing fires the same nudge.
    measured = measure_context_tokens(a.session) if pct > 0 else 0
    pct_hit = pct > 0 and window > 0 and measured >= (pct * window) // 100
    est = estimate_session_tokens(a.session) if thresh_abs > 0 else 0
    abs_hit = thresh_abs > 0 and est >= thresh_abs
    if (pct_hit or abs_hit) and not task.get("pressure_nudged"):
        seq = task.get("seq", task["id"][:8])
        # Prefer the real measurement in the copy (percent + tokens); fall back to the
        # byte-size estimate's token count when only the absolute trigger fired.
        if measured > 0:
            pct_now = round(measured * 100 / window) if window else 0
            left = max(0, 100 - pct_now)
            # Report BOTH used and remaining so the figure can't be misread against
            # Claude's native "% left" indicator. The nudge fires as the window FILLS
            # (used ≥ checkpoint_pct), i.e. precisely when little context is left.
            amount = "~%d%% used · ~%d%% left (~%dk/%dk tokens)" % (
                pct_now, left, measured // 1000, window // 1000)
            note = "proactive checkpoint nudge (~%d%% used, ~%dk tokens)" % (pct_now, measured // 1000)
        else:
            amount = "large (~%dk tokens)" % (est // 1000)
            note = "proactive checkpoint nudge (~%dk tokens)" % (est // 1000)
        if mark_pressure_nudged(task):
            touch(task, note=note)
            save_task(task)
        # Name the acting hub by its ordinal when it resolves (#463); data-gated so a
        # non-rostered session keeps the original "This session's" phrasing.
        olabel = ordinal_label(task, a.session)
        who = ("Hub session %s's" % olabel) if olabel else "This session's"
        line = ("[task-station] %s context is %s and nearing auto-compaction. "
                "Run `/todo save` NOW to capture a STRUCTURED checkpoint of task %s from "
                "full context — it is a better, task-shaped compaction than the generic "
                "auto-summary. Then continue, or open a fresh session and `/todo %s` to "
                "resume from the digest." % (who, amount, seq, seq))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": line}}))
        return
    # 2. Light staleness nudge — only when pressure did not fire.
    if not digest_stale(task):
        return
    # Milestone gate: hold the nudge until N meaningful events have accrued since the
    # last refresh (0/off = fire on any staleness, the pre-1.61 behaviour).
    if milestone > 0 and digest_events(task) < milestone:
        return
    line = ("[task-station] The attached task's digest looks stale (work happened "
            "since the last refresh). Before finishing, refresh `--state` in one line "
            "(or tick a `--step-done` / add a `--decision`) so a resume stays current.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop", "additionalContext": line}}))


def _split_refs(ref):
    """Split a `--task` value into individual refs: comma-separated, each
    whitespace-trimmed, empties dropped. A single ref is just a list of one.

    Shared by every batchable mutating subcommand (done / update / pin / unpin /
    add-project) so they all honor the same contract: one result line per ref, a
    bad ref reported but never aborting the rest."""
    return [r.strip() for r in (ref or "").split(",") if r.strip()]


# --- WS2 child-close mirror --------------------------------------------------
# The per-task event feed + `add_event` (with its EVENTS_KEEP / EVENT_TEXT_MAX
# caps) are the canonical definitions up beside `add_log`.


def _mirror_child_close(task, session=None):
    """When a task that was `spawned-from` a parent closes, append a `child` event
    to each parent's feed so a session on the parent hears its child wrapped up.

    Fully graceful: no `related` list, a `related` entry that isn't a spawned-from
    edge, or a parent that no longer loads are each skipped — a bare task closing
    touches nothing. Saves each parent it updates."""
    for edge in task.get("related", []) or []:
        if edge.get("kind") != "spawned-from":
            continue
        parent = load_task(edge.get("id"))
        if not parent:
            continue
        add_event(parent, "child",
                  "child #%s closed: %s" % (task.get("seq"), task.get("title")),
                  session)
        save_task(parent)


def _delegate_module():
    """Import the sibling delegate module (lib/delegate/delegate.py) in-process,
    or None if it can't be imported. Used by the reap-on-close path; a missing/
    broken delegate must never break closing a task."""
    try:
        d = os.path.join(BASE, "delegate")
        if d not in sys.path:
            sys.path.insert(0, d)
        import delegate as _dg
        return _dg
    except Exception:
        return None


def _reap_task_workers(task, session=None):
    """On close, stop this task's still-LIVE `--bg` worker sessions so finished
    workers don't linger/respawn in Agent View (#464/#465). Delegates the airtight
    reap to `delegate.reap_task_workers`, passing the task's session roster
    (`session_meta`) and the closing/current `session` sid so the reap can enforce its
    full safety predicate — a session is reaped ONLY IF it is a seq-matched registry
    worker AND role==worker in the roster AND task-station-named AND not busy AND not
    the current session (see delegate.reap_task_workers). Then records a `stop` ledger
    entry (actor = the closing hub `session`) and flips the roster status to `stopped`
    for each reaped worker. Mutates `task` in place (ledger + roster); the caller
    saves. Wholly best-effort: never raises — closing a task must not fail because
    reaping failed."""
    seq = task.get("seq")
    if seq is None:
        return
    try:
        dg = _delegate_module()
        if dg is None:
            return
        roster = task.get("session_meta") or {}
        reaped = dg.reap_task_workers(seq, roster=roster, current_sid=session)
    except Exception:
        return
    for sid in (reaped or []):
        try:
            add_ledger(task, "stop", worker_sid=sid, actor_sid=session,
                       detail="reaped on close")
            if sid in (task.get("session_meta") or {}):
                register_worker_session(task, sid, status="stopped")
        except Exception:
            pass


# ---- SessionStart orphan sweep -----------------------------------------------
# A `--bg` worker outliving its hub is NORMAL (that is what makes it adoptable), but a
# hub that CRASHED leaves its workers running forever — burning tokens and RAM and
# cluttering Agent View. Once per SessionStart we stop exactly those: task-station
# workers whose SPAWNING HUB SESSION IS GONE.
#
# Liveness comes from live_sessions, the existing process-state layer: Claude Code
# drops one `<PID>.json` per RUNNING process and `running()` returns only rows whose
# pid passes `os.kill(pid, 0)`. Nothing here reads the file's `status` field —
# `busy|idle` describes the MODEL TURN, not whether the session exists, so a hub
# sitting idle waiting for the user to type is ALIVE and its workers survive.
#
# The kill is delegated to `delegate.reap_task_workers`, which already enforces the
# full identity predicate and de-registers each worker from the supervisor BEFORE
# killing its process group, so it cannot be respawned.

# Workers younger than this are skipped: a just-spawned worker's hub may not have its
# session file on disk yet, which would make a live hub look dead.
ORPHAN_SWEEP_GRACE_SECS = 120


def _live_session_ids():
    """The set of session ids CURRENTLY running, or None meaning "unknown".

    None is the absence-of-evidence signal, and every caller must treat it as
    "reap nothing" — never as "everything is dead". It is returned when the sessions
    dir is unreadable, when `running()` fails, and when the live set comes back EMPTY:
    a machine actually running this sweep has at least its own process, so an empty
    listing points at a mis-resolved dir rather than a dead world."""
    try:
        import live_sessions
        if not os.listdir(live_sessions.sessions_dir()):
            return None
        rows = live_sessions.running()
    except Exception:
        return None
    sids = {r.get("session_id") for r in (rows or []) if r.get("session_id")}
    return sids or None


def _orphaned_workers(current_sid=None, now=None):
    """The `(seq, worker_sid)` pairs whose spawning hub is gone. Empty whenever
    anything at all is unknown.

    Reads only the delegate registry + the live-session set; performs no kill and no
    mutation, so it is the whole decision half of the sweep and is tested directly.

    Every gate below requires POSITIVE evidence, so each unknown fails safe:
    a registry entry with no recorded `name` (written before delegate started storing
    one) or no recorded `spawner` is skipped rather than guessed at — it simply never
    gets swept, and self-heals the next time that worker is registered."""
    live = _live_session_ids()
    if live is None:
        return []
    if current_sid:
        # The sweeping session is alive by definition, and this early in SessionStart
        # its own session file may not exist yet — never infer its death.
        live = set(live) | {current_sid}
    dg = _delegate_module()
    if dg is None:
        return []
    try:
        reg = dg.load_reg()
    except Exception:
        return []
    if not isinstance(reg, dict):
        return []
    now = _now() if now is None else now
    out = []
    for e in reg.values():
        if not isinstance(e, dict):
            continue
        sid, seq = e.get("session_id"), e.get("seq")
        if not sid or seq is None:
            continue
        if current_sid and sid == current_sid:
            continue                        # (2) never the sweeping session itself
        if not dg._is_ts_worker_name(e.get("name")):
            continue                        # (1) only OUR workers, never a foreign agent
        spawner = e.get("spawner")
        if not spawner or spawner in live:
            continue                        # unknown provenance, or the hub is alive
        started = e.get("started_ts")
        if started is None:
            started = e.get("ts")
        try:
            if started is None or (now - float(started)) < ORPHAN_SWEEP_GRACE_SECS:
                continue                    # (5) too new / no age → leave it alone
        except (TypeError, ValueError):
            continue
        out.append((seq, sid))
    return out


def sweep_orphan_workers(current_sid=None, adapter=None, now=None):
    """Stop every task-station worker whose spawning hub session is gone.

    Returns the list of `(seq, sid)` pairs actually reaped — empty when there is
    nothing to do OR when anything is unknown. Each reap is recorded on its task as a
    `stop` ledger entry (actor = the sweeping session) and flips the roster status to
    `stopped`, so a sweep that stops work someone still needed is visible in
    `/todo <n>` rather than looking like a crash.

    Inherits the `reap_workers_on_done` kill switch for free (the reap itself is gated
    on it), so a user who turned reaping off gets no sweep either.

    Wholly best-effort: never raises. `adapter` is injectable for tests; production
    passes None and `reap_task_workers` builds the real one."""
    reaped = []
    try:
        cands = _orphaned_workers(current_sid=current_sid, now=now)
    except Exception:
        return reaped
    if not cands:
        return reaped
    dg = _delegate_module()
    if dg is None:
        return reaped
    for seq, sid in cands:
        try:
            task = resolve_ref(str(seq))
            if not task:
                continue
            got = dg.reap_task_workers(seq, adapter=adapter,
                                       roster=task.get("session_meta") or {},
                                       current_sid=current_sid, only_sids=[sid])
        except Exception:
            continue                        # a failed reap never aborts the others
        for s in (got or []):
            reaped.append((seq, s))
            try:
                _record_orphan_reap(task["id"], s, current_sid)
            except Exception:
                pass                        # the worker is already stopped; logging is
                                            # best-effort and must not mask that
    return reaped


def _record_orphan_reap(task_id, worker_sid, actor_sid):
    """Log one orphan reap onto its task: a `stop` ledger entry naming the worker and
    the sweeping session, plus a roster flip to `stopped`.

    Goes through `mutate` (the required read-modify-write path) rather than
    load-then-save, so a sweep appending to the ledger can't clobber a concurrent
    writer on the same task."""
    def _apply(t):
        add_ledger(t, "stop", worker_sid=worker_sid, actor_sid=actor_sid,
                   detail="reaped on session start: orphaned (spawning hub gone)")
        if worker_sid in (t.get("session_meta") or {}):
            register_worker_session(t, worker_sid, status="stopped")
        t["updated_ts"] = _now()
    mutate(task_id, _apply)


def cmd_sweep_orphans(a):
    """`task-station sweep-orphans --session <sid>` — the SessionStart hook entry point.

    Prints one line per reap to STDERR (stdout carries the hook's JSON and must stay
    clean) and exits 0 no matter what: a sweep must never slow down or fail a session
    start."""
    try:
        reaped = sweep_orphan_workers(current_sid=getattr(a, "session", None))
    except Exception:
        return
    for seq, sid in reaped:
        sys.stderr.write("[task-station] reaped orphaned worker %s (task #%s) — its "
                         "spawning hub session is gone\n" % (sid[:8], seq))


def _done_gate_line(task):
    """THE DONE GATE — one warning line, or None.

    Closing a task whose record contradicts itself is the LAST chance to fix it: the
    digest outlives the session, and a closed task is what someone reads a year later
    to find out what was decided. So it WARNS and never blocks — refusing a close would
    trap work for a bookkeeping reason. Fail-open."""
    try:
        gate = _heal.gate_line(task)
    except Exception:
        return None
    if not gate:
        return None
    return ("  ⚠ %s Closing leaves that record as the permanent one — reconcile now if "
            "it still matters." % gate)


def _close_one(ref, session):
    """Close a single task by seq/id ref and return one human result line.

    Detaches every session linked to the task so none can silently reopen it.
    Returns a no-match / already-closed / closed line — never raises — so a
    caller closing a comma list can keep going past a bad ref."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "No task matching '%s'." % ref
    if is_closed(task):
        return "Task [%s] %s is already closed." % (task["id"][:8], task["title"])
    if task.get("provisional"):
        # Untouched auto-tracked task: closing it leaves no closed-task litter —
        # GC it instead. Detach every linked session first.
        tid, ttl = task["id"][:8], task["title"]
        for sess in list(task.get("sessions", [])):
            if get_link(sess) == task["id"]:
                clear_link(sess); clear_count(sess); clear_edit_markers(sess)
        delete_task(task["id"])
        return "Discarded provisional task [%s] %s (auto-tracked, never engaged)." % (tid, ttl)
    # Read the heal gate BEFORE closing — the "days on an active task" limb stops
    # applying the moment the status flips, and this is the last chance to warn.
    gate = _done_gate_line(task)
    task["status"] = STATUS_CLOSED              # close from open OR active
    stamp_closed(task)                          # real moment it entered closed
    touch(task, session=session, note="closed (by id)")
    _reap_task_workers(task, session)           # stop still-live --bg workers (#464)
    save_task(task)
    _mirror_child_close(task, session)          # tell any spawned-from parent's feed
    _obsidian_event(task, "closed")
    _stream_emit("task.status", task,
                 {"status": task.get("status"), "closed_ts": task.get("closed_ts")}, session)
    # Detach EVERY session linked to this task so none can silently reopen it.
    for sess in list(task.get("sessions", [])):
        if get_link(sess) == task["id"]:
            clear_link(sess)
            clear_count(sess)
            clear_edit_markers(sess)   # closing is a deliberate wrap-up — don't let the gate block
    line = "Closed task [%s] %s. Reopen later with /todo." % (task["id"][:8], task["title"])
    return (line + "\n" + gate) if gate else line


def _maybe_close_session_window(session):
    """Best-effort auto-close of THIS session's terminal window after a no-arg
    /done, gated on the opt-in `done_closes_window` config (default OFF). No-op
    unless the user opted in — we cannot tell a human-typed /done from a model
    Skill-tool /done, so the destructive close is opt-in, not intent-detected.
    When enabled, spawns close-session-window.sh detached (it resolves the tty
    synchronously, then closes ~1s later from a process that outlives this shell).
    Swallows every failure — never raises, never blocks the close. The `--task`
    path never calls this."""
    try:
        import config
        if not config.done_closes_window_enabled():
            return
        script = os.path.join(BASE, "close-session-window.sh")
        if not os.path.exists(script):
            return
        subprocess.Popen(["bash", script, "--detach", "--after", "1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def cmd_done(a):
    # Two modes:
    #   --task REF  → close any task by seq/id from anywhere (no session needed).
    #   --session   → close the task attached to this session (the /done path).
    ref = getattr(a, "task", None)
    if ref:
        # --task accepts a comma-separated list (e.g. "1,2,5"): close each ref,
        # print one result line per task, and tolerate a mix of valid/invalid —
        # a bad ref is reported but doesn't abort the rest. A single number is
        # just a list of one.
        refs = _split_refs(ref)
        if not refs:
            print("No task matching '%s'.\n\n%s" % (ref, _format_list()))
            return
        for r in refs:
            print(_close_one(r, a.session or None))
        maybe_refresh_board()   # once after the batch — the board's lifecycle changed
        return

    if not a.session:
        print("Pass --task <id-or-number> to close a specific task, "
              "or --session <id> to close the session's attached task.")
        return
    task_id = get_link(a.session)
    task = load_task(task_id) if task_id else None
    if not task:
        print("No task is attached to this session. Nothing to close.")
        return
    if task.get("provisional"):
        # Untouched auto-tracked task: GC instead of leaving a closed-task husk.
        tid, ttl = task["id"][:8], task["title"]
        delete_task(task["id"])
        clear_link(a.session)
        clear_count(a.session)
        clear_edit_markers(a.session)
        maybe_refresh_board()   # a discarded task leaves the board too
        _maybe_close_session_window(a.session)   # opt-in; no-op unless enabled
        print("Discarded provisional task [%s] %s (auto-tracked, never engaged) and "
              "detached this session." % (tid, ttl))
        return
    # The heal gate, read BEFORE the status flips (see _done_gate_line).
    gate = _done_gate_line(task)
    task["status"] = STATUS_CLOSED          # close from open OR active
    stamp_closed(task)                      # real moment it entered closed
    touch(task, session=a.session, note="closed")
    _reap_task_workers(task, a.session)     # stop still-live --bg workers (#464)
    save_task(task)
    _mirror_child_close(task, a.session)    # tell any spawned-from parent's feed
    _obsidian_event(task, "closed")
    _stream_emit("task.status", task,
                 {"status": task.get("status"), "closed_ts": task.get("closed_ts")}, a.session)
    clear_link(a.session)   # detach so a later message can't silently reopen it
    clear_count(a.session)
    clear_edit_markers(a.session)   # deliberate wrap-up — don't let the Stop gate block
    maybe_refresh_board()   # board must show this closed NOW, not wait for the Stop hook
    _maybe_close_session_window(a.session)   # opt-in; no-op unless enabled
    print("Closed task [%s] %s and detached this session. Reopen later with /todo."
          % (task["id"][:8], task["title"]))
    if gate:
        print(gate)


def cmd_delete(a):
    """HARD-delete a single task and detach any session linked to it.

    Maintenance escape hatch only — the lifecycle is close-not-delete (`done`
    closes the task and keeps its record; this removes the record entirely).
    Hidden from `--help`, the config board, and the README; documented only in
    `guidance` so the model can still reach for it. Removes EXACTLY the one
    resolved task — never the store."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    tid, seq, title = task["id"], task.get("seq"), task["title"]
    # Detach every session linked to this task so none is left pointing at a ghost
    # (mirrors the provisional-discard path in _close_one).
    for sess in list(task.get("sessions", [])):
        if get_link(sess) == tid:
            clear_link(sess)
            clear_count(sess)
            clear_edit_markers(sess)
    delete_task(tid)
    # Purge the task's exported notes so a hard-delete doesn't leave orphans behind:
    # the vault mirror note + any generic-export notes recorded in locatable sidecar
    # indexes, dropping each sidecar entry and refreshing that dir's index.md. Fully
    # best-effort — a gone/unwritable vault must never abort the delete, and the
    # tombstone is emitted regardless.
    try:
        import obsidian_sync, export as _export
        owner = _owner()
        v = _obsidian_vault()
        if v:
            obsidian_sync.remove_task_note(
                tid, obsidian_sync.owner_dir(obsidian_sync.plugin_dir(v), owner))
        for d in _export_dirs():
            scoped = obsidian_sync.owner_dir(d, owner)   # only this owner's subtree
            try:
                if obsidian_sync.remove_task_note(tid, scoped):
                    _export.rebuild_index(scoped)   # drop the note's line from the listing
            except Exception:
                pass
    except Exception:
        pass
    # Tombstone AFTER the row is gone — persist=False (nothing to persist the counter
    # onto), the in-hand dict carries the final n.
    _stream_emit("task.deleted", task, {}, getattr(a, "session", None), persist=False)
    print("Deleted task [%s] #%s %s." % (tid[:8], seq, title))


def _stream_human(e):
    """One compact human line for a stream event (the non-JSON `stream` view)."""
    t = e.get("task") or {}
    data = e.get("data") or {}
    preview = ""
    if e.get("event") == "task.updated":
        preview = ",".join(data.get("changed") or [])
    elif e.get("event") == "task.event":
        preview = "%s: %s" % (data.get("kind", ""), data.get("text", ""))
    elif e.get("event") == "task.status":
        preview = data.get("status", "")
    elif e.get("event") == "task.relation":
        preview = "→ #%s" % (data.get("other") or {}).get("seq")
    elif data.get("redacted"):
        preview = "(redacted)"
    return "n%-4s %s  %-16s #%-4s %s" % (
        e.get("n"), e.get("ts"), e.get("event"), t.get("seq"), preview)


def cmd_stream(a):
    """Read / maintain the durable Tasktrail ledger.

      --since <cursor> | --tail [N]   read events (cursor = 0-based global index)
      --json                          emit raw JSONL envelopes (else a human line)
      --backfill                      emit a task.snapshot per still-unstreamed task
      --verify                        check per-task n continuity + shard order

    Read paths do NOT swallow errors — a corrupt/unreadable ledger is reported."""
    import stream
    if getattr(a, "backfill", False):
        try:
            present = {(e.get("task") or {}).get("uuid") for e in stream.read_events()}
        except Exception as ex:
            sys.stderr.write("stream --backfill: cannot read existing stream: %s\n" % ex)
            return
        made = 0
        for t in sorted(all_tasks(), key=lambda t: t.get("seq") or 0):
            u = t.get("uuid") or t.get("id")
            if u in present:
                continue     # already represented — idempotent
            if _stream_emit("task.snapshot", t, _stream_digest(t), None) is not None:
                made += 1
        print("Backfilled %d task snapshot(s)." % made)
        return
    if getattr(a, "verify", False):
        res = stream.verify()
        if res["ok"]:
            print("stream verify OK — %d event(s) across %d task(s); "
                  "continuity + shard order intact." % (res["events"], res["tasks"]))
        else:
            print("stream verify FAILED (%d issue(s)):" % len(res["issues"]))
            for i in res["issues"]:
                print("  - %s" % i)
        return
    events = list(stream.read_events())
    tail = getattr(a, "tail", None)
    if tail is not None:
        k = tail if isinstance(tail, int) and tail > 0 else 20
        events = events[-k:]
    else:
        since = getattr(a, "since", None)
        if since is not None:
            try:
                cur = int(since)
            except (TypeError, ValueError):
                cur = 0
            events = events[cur:]
    as_json = getattr(a, "json", False)
    for e in events:
        print(json.dumps(e, ensure_ascii=False, sort_keys=True) if as_json
              else _stream_human(e))


def cmd_redact(a):
    """Right-to-be-forgotten: rewrite EVERY shard replacing task N's event payloads
    with a stub, bump the manifest generation, and append a task.redacted marker.
    Also rewrites the external tee when configured. Read/maintenance path — surfaces
    errors rather than swallowing them."""
    import stream, config
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    u = task.get("uuid") or task["id"]
    stubbed = stream.stub_task(u)
    tee = config.stream_dir()
    if tee:
        stream.stub_task(u, base=tee)
    gen = stream.bump_generation()
    if tee:
        stream.bump_generation(base=tee)
    # The task still exists (redact ≠ delete) — persist the marker's counter normally.
    _stream_emit("task.redacted", task, {"generation": gen}, getattr(a, "session", None))
    # Mark the task redacted so `export --prune` reconciles away its exported notes
    # (their content is being forgotten) even in dirs offline at redact time. Guarded.
    try:
        mutate(task["id"], lambda t: t.__setitem__("redacted", True))
    except Exception:
        pass
    print("Redacted task #%s [%s]: stubbed %d payload(s); manifest generation now %d."
          % (task.get("seq"), task["id"][:8], stubbed, gen))


def _live_marker(task):
    """` ⧉N` when more than one session is concurrently attached to this task,
    else "". Every open task trivially has ≥1 live session, so the marker only
    appears on the interesting case (N > 1) and never clutters the common row."""
    n = live_session_count(task)
    return " ⧉%d" % n if n > 1 else ""


# ------------------------------------------------- native Tasks interop ----
# READ-ONLY bridge to Claude Code's in-session native Tasks (see native_tasks.py).
# Native Tasks = ephemeral, per-session orchestration; task-station = the durable
# cross-session console. We surface native lists so nothing is invisible, and let
# `adopt` promote a native item worth tracking durably — but NEVER write the native
# store. Every helper degrades to "nothing" if native_tasks or the store is absent.


def _native_lists():
    """Best-effort recent native task lists — [] when the module/store is
    unavailable. Guarded so a missing native_tasks.py or unreadable ~/.claude/tasks
    never breaks a board render."""
    try:
        import native_tasks
        return native_tasks.list_native_lists()
    except Exception:
        return []


def _format_native():
    """Compact read-only listing of Claude Code's recent native task lists: one
    section per list (short uuid + relative mtime), each item as `<glyph> <id>
    <subject>`. Says so plainly when there's nothing recent. Read-only — the native
    store is never written."""
    try:
        import native_tasks
    except Exception:
        return "Native Tasks interop unavailable (native_tasks.py missing)."
    lists = _native_lists()
    if not lists:
        return ("No recent native task lists. Claude Code 2.1+ writes in-session "
                "tasks under ~/.claude/tasks; nothing recent to show.")
    out = ["Native Tasks (Claude Code in-session; READ-ONLY) — adopt one to track it "
           "durably:  /todo adopt <list>:<id>"]
    for L in lists:
        out.append("")
        out.append("%s  (%s)" % (L["short"], rel_time(L["mtime"])))
        for it in L["items"]:
            glyph = native_tasks.STATUS_GLYPH.get(it["status"], "○")
            out.append("  %s %s %s" % (glyph, it["id"], it["subject"]))
    return "\n".join(out)


def _native_footer_line():
    """One-line NATIVE summary for the terminal board footer, or None. Shown ONLY
    when at least one recent native list has OPEN items — a pointer, not the full
    dump (the board stays lean; `/todo native` shows the detail)."""
    lists = [L for L in _native_lists() if L["open_count"] > 0]
    if not lists:
        return None
    n_open = sum(L["open_count"] for L in lists)
    return ("NATIVE: %d open item(s) in %d Claude Code native list(s) — /todo native "
            "to view · /todo adopt <list>:<id> to track durably"
            % (n_open, len(lists)))


def cmd_native(a):
    """`task-station native` (and `/todo native`) — read-only listing of Claude
    Code's recent in-session native task lists."""
    print(_format_native())


def cmd_adopt(a):
    """`task-station adopt --native <list-prefix>:<id>` — promote a Claude Code
    native task into a durable station task. Creates it from the native item's
    subject/description (colour black/GENERAL, effort S), records provenance in the
    summary + activity log, and prints the created-task line. NEVER writes the
    native store (read-only interop)."""
    ref = (getattr(a, "native", None) or "").strip()
    if not ref:
        print("usage: adopt --native <list-prefix>:<id>")
        return
    try:
        import native_tasks
    except Exception:
        print("Native Tasks interop unavailable (native_tasks.py missing).")
        return
    L, item = native_tasks.find_native_item(ref)
    if not item:
        print("No native task matching '%s'. Run `native` to list recent native "
              "tasks, then adopt with <list-prefix>:<id>." % ref)
        return
    prov = "adopted from native task %s:%s" % (L["short"], item["id"])
    title = item["subject"] or "Adopted native task"
    desc = item["description"]
    summary = ("%s\n\n(%s)" % (desc, prov)) if desc else "(%s)" % prov
    task = new_task(title, summary, color="black", effort="s")
    create_with_seq(task)              # atomically mint the stable number + persist
    touch(task, note=prov)
    save_task(task)
    _stream_emit("task.created", task, _stream_created_data(task), getattr(a, "session", None))
    print("📋 Adopted native task %s:%s → task [%s] #%s %s"
          % (L["short"], item["id"], task["id"][:8], task["seq"], task["title"]))
    for line in cat_lines(task.get("color")):
        print(line)
    auto_enable_category(task.get("color"))


def _format_list(closed_limit=MAX_CLOSED_IN_LIST):
    # closed_limit caps how many closed tasks are shown (most recent first).
    # None means "show every closed task" (`/todo all`); an int shows that many
    # (`/todo closed` / `/todo closed N`). The default keeps the bare `/todo`
    # list short.
    ensure_seqs()                      # guarantee every task has its stable number
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    lines = []
    closed_total = sum(1 for t in listing if is_closed(t))
    capped = closed_limit is not None and closed_total > closed_limit
    if capped:
        shown = 0
        trimmed = []
        for t in listing:
            if is_closed(t):
                shown += 1
                if shown > closed_limit:
                    continue
            trimmed.append(t)
        listing = trimmed
    # Two sections: the board (open + active, glyph-distinguished) then closed.
    last_section = None
    for t in listing:
        section = "CLOSED" if is_closed(t) else "OPEN"
        if section != last_section:
            lines.append("")
            lines.append(section)
            last_section = section
        tag = cat_tag(t.get("color"), pad=True)
        eff = effort_cell(t.get("effort"))
        marker = _live_marker(t)
        g = status_glyph(t)            # leading lifecycle glyph, before the number
        # Compact progress rollup folded INTO the 40-wide Task cell (no new column,
        # so the grid + the /todo verbatim contract are untouched): "Title  ✓2/5",
        # only when the task has steps. Truncated with the title at 40 chars.
        d, m = step_progress(t)
        title_cell = ("%s  ✓%d/%d" % (t["title"], d, m)) if m else t["title"]
        if tag:
            lines.append("%s %3d  %-40.40s  %s  %s  %s%s"
                         % (g, t["seq"], title_cell, tag, eff, rel_time(t.get("updated_ts")), marker))
        else:
            lines.append("%s %3d  %-40.40s  %s  %s%s"
                         % (g, t["seq"], title_cell, eff, rel_time(t.get("updated_ts")), marker))
    if capped:
        lines.append("     … %d older closed task(s) hidden  ·  show more with /todo closed N "
                     "or /todo all  ·  reachable by number: /todo <n> or /done <n>"
                     % (closed_total - closed_limit))
    lines.append("")
    lines.append(status_legend())
    lines.append(effort_legend())
    if cats:
        lines.append(cats.legend())
    # A one-line NATIVE pointer — only when Claude Code's in-session native tasks
    # have open items — so the durable board stays aware of them without dumping
    # the whole list (see `/todo native`). Kept lean and conditional.
    nf = _native_footer_line()
    if nf:
        lines.append("")
        lines.append(nf)
    lines.append(commands_footer())
    return ("Tasks (not-closed first, then by recent activity):\n"
            + "\n".join(lines))


def _md_escape(text):
    """Escape the characters that would break a GitHub table cell."""
    return (text or "").replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_effort(effort):
    """`▰▱` gauge + size for a Markdown cell — same content as the ASCII column
    (reuses effort_cell) but without its fixed-width padding."""
    return effort_cell(effort).rstrip()


def _md_task_row(task):
    """One GitHub-table row: `|  | # | Task | Category | Effort | Activity |`.
    The leading STATUS column holds the lifecycle glyph (`●` active / `○` open /
    `✕` closed); the `#` cell holds the bare seq number only. The Task cell carries
    the ` ⧉N` live-session marker (when >1), mirroring the ASCII list; the Category
    cell keeps the `<emoji> [TAG]` intact."""
    st = task_status(task)
    status_cell = STATUS_GLYPH_CLOSED if st == STATUS_CLOSED else STATUS_GLYPH.get(st, "")
    # Compact progress rollup appended to the Task cell (no new column), only when
    # the task has steps — mirrors the ASCII list's "✓N/M".
    d, m = step_progress(task)
    progress = ("  ✓%d/%d" % (d, m)) if m else ""
    return "| %s | %s | %s | %s | %s | %s |" % (
        status_cell,
        task.get("seq", ""),
        _md_escape(task["title"]) + _live_marker(task) + progress,
        cat_tag(task.get("color")),
        _md_effort(task.get("effort")),
        rel_time(task.get("updated_ts")),
    )


_MD_HEADER = ("|  | # | Task | Category | Effort | Activity |\n"
              "|:-:|--:|------|----------|--------|----------|")


def _format_list_md(closed_limit=MAX_CLOSED_IN_LIST):
    """Markdown form of the /todo list — what the skill now prints VERBATIM (no
    hand-transcription). Two GitHub tables, Open first then Closed, preserving the
    tracker's ordering; columns are a centered STATUS glyph · # (stable seq,
    right-aligned) · Task · Category (`<emoji> [TAG]`) · Effort (`▰▱` bar + size)
    · Activity (relative time). Honors the same closed-limit logic as the ASCII
    list (default MAX_CLOSED_IN_LIST, `all`, or N) and repeats the hidden-older
    note after the Closed table, then the Commands footer mini-table."""
    ensure_seqs()
    listing = sorted_tasks()
    if not listing:
        return ("No tasks yet. One will be tracked automatically once the work "
                "in a session becomes clear, or say so explicitly.")
    board_tasks = [t for t in listing if is_on_board(t)]   # open + active
    closed_tasks = [t for t in listing if is_closed(t)]
    closed_total = len(closed_tasks)
    capped = closed_limit is not None and closed_total > closed_limit
    shown_closed = closed_tasks[:closed_limit] if capped else closed_tasks

    out = []
    if board_tasks:
        out.append("### Open")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in board_tasks)
    if shown_closed:
        if out:
            out.append("")
        out.append("### Closed")
        out.append(_MD_HEADER)
        out.extend(_md_task_row(t) for t in shown_closed)
    if capped:
        out.append("")
        out.append("… %d older closed task(s) hidden — show more with `/todo closed N` "
                   "or `/todo all`." % (closed_total - closed_limit))
    out.append("")
    out.append("_%s new · %s in progress · %s closed_" % (
        STATUS_GLYPH[STATUS_OPEN], STATUS_GLYPH[STATUS_ACTIVE], STATUS_GLYPH_CLOSED))
    out.append(commands_footer_md())
    return "\n".join(out)


# ---------------------------------------------------------------- briefing ----
# A task's "briefing" is derived/curated context that makes a resume load where
# the work STANDS — never via an LLM. Three sources:
#   • files  — recently-edited paths, captured deterministically by the
#     PostToolUse hook (touch-file), deduped + capped, most-recent-last.
#   • state  — a short MODEL-curated "where it stands / next step" string,
#     maintained with `update --state` (the model is already in the loop).
#   • prs    — DERIVED on render by scanning the activity log/summary/state for
#     PR URLs (GitHub + Azure DevOps); never stored.

# GitHub `…/pull/<n>` and Azure DevOps `…/pullrequest/<n>` (dev.azure.com and the
# generic `_git/<repo>/pullrequest/<n>` form). Path chars stay URL-safe so a
# trailing `.`/`)`/space in a note never gets swallowed; `\d+` bounds the tail.
_PR_URL_RE = re.compile(
    r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+'
    r'|https://dev\.azure\.com/[\w%./+-]+?/pullrequest/\d+'
    r'|https://[\w.-]+/[\w%./+-]+?/_git/[\w%./+-]+?/pullrequest/\d+',
    re.IGNORECASE,
)


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

# OWNERSHIP RULE — decided, not an accident of there being nothing to check yet.
# `related` (and the later `mentions`) may name a task in ANOTHER person's brain;
# `depends-on` and `parent` may NOT, because both are COMPUTED OVER — roll-ups and
# unblocked-work queries — and compute requires freshness. A stale foreign edge would
# make those answers silently wrong rather than loudly unavailable. v1 has no
# resolvable foreign handle at all (none can exist before sync lands), so today this
# only sharpens the error message; it is written as the named rule because this is the
# ONE place sync has to teach when foreign refs become real.
_LOCAL_ONLY_KINDS = frozenset(("depends-on", "parent"))

# An interbrain handle is `<owner>-<seq>` (see the board's handle chip). A local seq is
# all-digits and a local `<seq>-<ordinal>` starts with a digit, so requiring a leading
# LETTER separates the two grammars. Only consulted after local resolution has already
# failed, so a false positive can never mis-resolve a real task — at worst it picks the
# more specific of two "no such task" messages.
_FOREIGN_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*-\d+$")


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


def _memo_ledger(memo):
    """The inline ack roster for a memo: 'ab12cd34, 9e01f2aa' | '(none yet)'. An ack
    carrying a disposition renders it inline ('ab12cd34→memory'), so the ledger shows
    what each session DID with the memo, not merely that it saw it.

    A RETRO-filled disposition reads 'ab12cd34→noop (retro)'. That tag is the whole
    point: a disposition a later reconcile pass filled in is a reasonable guess about a
    session that no longer exists, and it must never read as the acking session's own
    answer."""
    out = []
    for a in (memo.get("acks") or []):
        if not a.get("sid"):
            continue
        disp = a.get("disposition") or {}
        kind = disp.get("kind")
        if not kind:
            out.append(a["sid"][:8])
            continue
        out.append("%s→%s%s" % (a["sid"][:8], kind,
                                " (retro)" if _heal.is_retro(disp) else ""))
    return ", ".join(out) if out else "(none yet)"


def _memo_line(memo, viewer=None, width=100):
    """One rendered memo row: [⚑ unacked by you ·] id8 · age · from · preview · ledger.
    The ⚑ flag shows only when the memo is still pending for `viewer`. A memo that
    declares `--corrects` carries a CORRECTS tag so its weight is visible in the roster
    without opening it."""
    body = " ".join((memo.get("text") or "").split())
    if len(body) > width:
        body = body[:width - 1].rstrip() + "…"
    src = _memo_src_label(memo.get("from_sid"), memo.get("from_task"))
    flag = "⚑ unacked by you · " if (viewer and _memo_pending_for(memo, viewer)) else ""
    corrects = memo_corrections(memo)
    tag = (" [CORRECTS: %s]" % ", ".join(corrects)) if corrects else ""
    return ('  • %s%s (%s, from %s)%s "%s" — acked by: %s'
            % (flag, memo["id"][:8], rel_time(memo.get("ts")), src, tag, body,
               _memo_ledger(memo)))


def _memo_detail_lines(task, viewer):
    """The default-detail "Memos:" section: everything still unacked-by-viewer first
    (flagged), then the last few already-handled memos. Empty on a task with no memos."""
    memos = task.get("memos") or []
    if not memos:
        return []
    pending = [m for m in memos if _memo_pending_for(m, viewer)]
    handled = [m for m in memos if not _memo_pending_for(m, viewer)]
    out = ["", "Memos:"]
    for m in pending:
        out.append(_memo_line(m, viewer))
    for m in handled[-3:]:
        out.append(_memo_line(m, viewer))
    return out


def _format_memo_list(task, viewer=None):
    """`memo show` (no --id): the memo roster for one task, newest-last."""
    memos = task.get("memos") or []
    if not memos:
        return "(no memos)"
    seq = task.get("seq", task["id"][:8])
    out = ["Memos on task #%s [%s] (%d):" % (seq, task["id"][:8], len(memos))]
    for m in memos:
        out.append(_memo_line(m, viewer))
    return "\n".join(out)


def _format_memo_full(task, memo):
    """`memo show --id <prefix>`: the FULL, uncapped memo body + its ack ledger. A memo
    declaring `--corrects` shows its targets PROMINENTLY, above the body — the reader
    must see what this replaces before deciding what to do about it. The
    correction-language backstop fires here too, so a memo that reads like a correction
    but declares nothing still tells the reader to go check the durable stores."""
    src = _memo_src_label(memo.get("from_sid"), memo.get("from_task"))
    out = ["Memo %s on task #%s [%s]"
           % (memo["id"][:8], task.get("seq", task["id"][:8]), task["id"][:8]),
           "From:    %s (%s)" % (src, rel_time(memo.get("ts")))]
    corrects = memo_corrections(memo)
    if corrects:
        out.append("CORRECTS: %s" % ", ".join(corrects))
        out.append("         ↳ acking this REQUIRES a disposition that engages the "
                   "target (--decision / --memory / --noop \"<reason>\").")
    elif correction_language(memo.get("text")):
        out.append("⚠ reads like a correction but declares no --corrects target — if it "
                   "changes anything durable, update that store as part of acking.")
    out.append("Acked:   %s" % _memo_ledger(memo))
    # RETRO-filled dispositions get their provenance spelled out here — who filled it in,
    # when, and why — next to the ack's OWN unchanged timestamp. A reader must be able to
    # tell a later pass's reasonable guess from what the acking session actually said.
    for a in (memo.get("acks") or []):
        disp = a.get("disposition") or {}
        if not _heal.is_retro(disp):
            continue
        out.append("         ↳ %s→%s is RETRO: filled in by %s %s — %s. The ack itself "
                   "(%s) is unchanged. Recorded: %s"
                   % ((a.get("sid") or "?")[:8], disp.get("kind"),
                      str(disp.get("retro_by") or "?")[:8],
                      rel_time(disp.get("retro_ts")),
                      disp.get("retro_why") or "no reason recorded",
                      rel_time(a.get("ts")), disp.get("value") or "(no value)"))
    out.append("")
    out.append(memo.get("text") or "")
    return "\n".join(out)


def _format_detail(task, session, attached=True):
    out = []
    cur = task_status(task)
    # Header carries the glyph for board tasks (○ open / ● active); closed has none.
    glyph = (STATUS_GLYPH[cur] + " ") if cur in STATUS_GLYPH else ""
    out.append("Task [%s]  —  %s%s" % (task["id"][:8], glyph, status_display(cur).upper()))
    out.append("Title:   %s" % task["title"])
    if cats:
        out.append(cats.summary(task.get("color")))
    eff = task.get("effort")
    if eff in EFFORT_GAUGE:
        out.append("Effort:  %s %s (%s)" % (EFFORT_GAUGE[eff], eff, EFFORT_WORD[eff]))
    out.append("Created: %s (%s)" % (rel_time(task.get("created_ts")), _local_iso(task.get("created_at", ""))))
    out.append("Updated: %s" % rel_time(task.get("updated_ts")))
    # Live = sessions still attached right now (link resolves back to this task);
    # total = every session that ever touched it (append-only, never pruned).
    out.append("Live sessions: %d  (of %d ever attached)"
               % (live_session_count(task), len(task.get("sessions", []))))
    # Session TREE — hubs (main vs side-quest) with their spawned workers nested.
    # Absent on a task with no recorded sessions (bare tasks render as before).
    sess_lines = _session_block_lines(task)
    if sess_lines:
        out.append("")
        out.extend(sess_lines)
    # Worker provenance — append-only hub<->worker interaction ledger tail (#463).
    # Absent when the task has no ledger (bare tasks render exactly as before).
    led = task.get("ledger") or []
    if led:
        out.append("")
        out.append("  workers (provenance, last %d):" % min(len(led), 8))
        lmeta = task.get("session_meta") or {}
        for e in led[-8:]:
            who = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                   if e.get("actor_ordinal") is not None
                   else (e.get("actor") or "?")[:8])
            wname = ((lmeta.get(e.get("worker")) or {}).get("name")
                     or (e.get("worker") or "")[:8])
            out.append("    %s  %s %s → %s%s" % (
                rel_time(e.get("ts")), who, e.get("action"), wname,
                (" — " + e["detail"]) if e.get("detail") else ""))
    # Time/cost stats — active time (idle-gap-capped spans) + accumulated worker
    # cost. Omitted entirely for a brand-new task with neither recorded yet.
    stats = task_stats_line(task)
    if stats:
        out.append("Stats:   %s" % stats)
    # DIGEST (digest-first): goal → state → steps → decisions → artifacts. The
    # deterministic structured briefing that makes a resume load where the work
    # STANDS — never via an LLM. Supersedes the 1.15 "Briefing:" block; the full
    # Summary moves to the very end (below).
    goal = (task.get("goal") or "").strip()
    state = (task.get("state") or "").strip()
    steps = task.get("steps") or []
    decisions = task.get("decisions") or []
    prs = merged_prs(task)
    stories = merged_stories(task)
    files = task.get("files") or []
    projects = task.get("projects") or []
    if goal:
        out.append("")
        out.append("Goal:    %s" % goal)
    if state:
        out.append("")
        out.append("State (next):")
        out.append("  %s" % state)
    # The ACTIVE checklist only. A SUPERSEDED step has left it — it is not outstanding
    # work — and it counts in neither side of the n/m. Numbers stay the ORIGINAL
    # indices (so `--step-done 4` keeps meaning step 4 after step 2 was retired), which
    # is why the list can show gaps; renumbering would silently repoint every command a
    # reader had in hand. The retired steps stay in `/todo <n> history`, marked.
    if steps:
        active = _steps.live(steps)
        done, total = step_progress(task)
        retired = len(steps) - len(active)
        out.append("")
        if active:
            out.append("Steps (%d/%d done):" % (done, total))
            for i, s in active:
                mark = "✓" if _steps.is_done(s) else "☐"
                out.append("  %s %2d. %s" % (mark, i, _steps.text(s)))
        else:
            out.append("Steps:   (none active)")
        if retired:
            out.append("  (%d superseded step(s) — off the checklist and out of the "
                       "count; full text in `/todo %s history`)"
                       % (retired, task.get("seq", task["id"][:8])))
    # EVERY still-current decision, spine first then narrative — no age limit, no count
    # limit, and no "+N earlier" pointer, because nothing is folded away to point at.
    # REPLACED decisions (superseded / split / merged) are omitted ENTIRELY: they are
    # not "old", they are no longer true, and showing them is the failure this exists to
    # fix. They survive only in `history`. Pinning sorts a decision into the leading
    # spine block (marked ★); it no longer decides whether it appears at all.
    shown_decisions = _dec.digest_order(decisions)
    if shown_decisions:
        out.append("")
        out.append("Decisions:")
        for _i, d in shown_decisions:
            out.append("  • %s%s" % (DECISION_PIN_MARK if _dec.is_pinned(d) else "",
                                     _dec.text(d)))
    # Memos: correspondence handed to this task — anything still awaiting THIS viewer's
    # ack is flagged first (ack it before acting so two sessions don't double-implement),
    # then the last few already-handled. Absent on a task with no memos feed.
    out.extend(_memo_detail_lines(task, session))
    # Relation edges (spawned-from / --relate + derived reverse edges); absent when
    # the task has none in either direction. Full scan is fine at detail cadence.
    related_line = _related_line(task)
    if files or prs or stories or projects or related_line:
        out.append("")
        out.append("Artifacts:")
        if files:
            out.append("  Files (most recent last):")
            for p in files[-8:]:
                d = os.path.dirname(p) or "."
                out.append("    %s  —  %s" % (os.path.basename(p), d))
        if prs:
            out.append("  PRs:")
            for p in prs:
                line = p["url"]
                if p.get("desc"):
                    line += "  —  " + p["desc"]
                out.append("    %s" % line)
        if stories:
            out.append("  Stories:")
            for s in stories:
                line = s["url"]
                if s.get("desc"):
                    line += "  —  " + s["desc"]
                out.append("    %s" % line)
        if projects:
            out.append("  Repos:   %s" % ", ".join(projects))
        if related_line:
            out.append("  " + related_line)
    # Recent activity (the per-prompt `log`) — capped to a lean tail for the
    # default resume path. The dated milestone trail (`history`, via --log) is
    # NOT rendered here at all; both the full activity + the milestone log live
    # behind `/todo <n> history`.
    log = task.get("log", [])
    if log:
        out.append("")
        out.append("Recent activity (most recent last):")
        recent = log[-ACTIVITY_TAIL:]
        if len(log) > len(recent):
            out.append("  … older activity — /todo %s history"
                       % task.get("seq", task["id"][:8]))
        for e in recent:
            when = rel_time(_iso_to_ts(e.get("ts", "")))
            out.append("  • [%s] %s" % (when, e.get("note", "")))
    out.append("")
    if attached:
        out.append("This session is now ATTACHED to this task (id %s). Continue the work "
                   "described above; the user's next message resumes it. To close it, use /done."
                   % task["id"])
    else:
        # Read-only digest (e.g. `search --detail`): rendered WITHOUT attaching, so
        # don't claim the session took the task. Point at the open/history commands.
        out.append("(Read-only digest — this did NOT attach the task. Open it with "
                   "/todo %s, or /todo %s history for the full trail.)"
                   % (task.get("seq", task["id"][:8]), task.get("seq", task["id"][:8])))
    # Live process state (running Claude sessions) annotates the resume line below
    # with a ● busy/idle · age marker — computed once, guarded. The in-project
    # workers this task delegated into are now shown nested under their spawning hub
    # in the Sessions tree above, so they are NOT re-listed here (no duplication).
    live = _live_session_index()
    rt = _resume_target(task, session)
    resume = rt["command"] if rt else None
    if resume:
        out.append("")
        out.append("Resume the working session that holds this task's context "
                   "(cd + resume, one command):")
        hub_note = _live_note(live.get(rt.get("session"))) if rt else ""
        out.append("    Hub%s:  %s%s" % (
            " (pinned)" if task.get("pinned_session") else "", resume, hub_note))
    # Summary LAST — the stable description, after the at-a-glance digest + resume.
    out.append("")
    out.append("Summary:")
    out.append(task.get("summary") or "  (no summary recorded)")
    adv = ultracode_advisory(task)
    if adv:
        out.append("")
        out.append(adv)
    return "\n".join(out)


def _replaced_suffix(decisions):
    """The ` — N superseded` / ` — N replaced (1 superseded · 2 split)` tail on the
    history view's Decisions header, or "" when every decision is current.

    Worded by the kinds ACTUALLY present, so a task whose only reconcile was
    supersession still reads exactly as it did before split/merge existed, and a mixed
    task names each verb rather than hiding all three behind one word."""
    kinds = {}
    for d in decisions or []:
        rep = _dec.replacement(d)
        if rep is not None:
            kinds[rep[0]] = kinds.get(rep[0], 0) + 1
    total = sum(kinds.values())
    if not total:
        return ""
    words = {_dec.REPLACED_SUPERSEDED: "superseded",
             _dec.REPLACED_SPLIT: "split",
             _dec.REPLACED_MERGED: "merged"}
    order = [k for k in (_dec.REPLACED_SUPERSEDED, _dec.REPLACED_SPLIT,
                         _dec.REPLACED_MERGED) if kinds.get(k)]
    if len(order) == 1:
        return " — %d %s" % (total, words[order[0]])
    return " — %d replaced (%s)" % (
        total, " · ".join("%d %s" % (kinds[k], words[k]) for k in order))


def _format_history(task):
    """The on-demand `/todo <n> history` time-machine: the COMPLETE trail for one
    task — every decision, every dated milestone (the `history` field, written via
    `update --log`), and the full activity log — clearly sectioned under a brief
    goal/state header. READ-ONLY: it renders, never attaches/reopens/mutates.

    What this shows and the default digest does NOT is the RETIRED decisions —
    superseded, split, merged — plus the dated milestone log and the full activity
    log. It is no longer the only uncapped view of the decisions themselves: the
    digest stopped truncating by age, so the difference between the two surfaces is
    now exactly "is this still true", which is the difference that means something."""
    out = []
    cur = task_status(task)
    glyph = (STATUS_GLYPH[cur] + " ") if cur in STATUS_GLYPH else ""
    seq = task.get("seq", task["id"][:8])
    out.append("History — Task #%s [%s]  —  %s%s"
               % (seq, task["id"][:8], glyph, status_display(cur).upper()))
    out.append("Title:   %s" % task["title"])
    goal = (task.get("goal") or "").strip()
    if goal:
        out.append("Goal:    %s" % goal)
    state = (task.get("state") or "").strip()
    if state:
        out.append("State:   %s" % state)
    # The COMPLETE checklist — every step ever added, including the SUPERSEDED ones the
    # active checklist drops, each marked with what replaced it (or that nothing did).
    # This is the only surface that shows a retired step, and it is what makes the
    # supersede verb honest: nothing is deleted, so the record of what was once planned
    # survives, and `update --step-restore <n>` puts any of them back.
    all_steps = task.get("steps") or []
    if all_steps:
        _active, retired = _steps.counts(all_steps)
        out.append("")
        out.append("Steps (%d, oldest first%s):"
                   % (len(all_steps),
                      (" — %d superseded" % retired) if retired else ""))
        for i, s in enumerate(all_steps, 1):
            label = _steps.replacement_label(s)
            if label is not None:
                out.append("  %2d. %s%s  — %s"
                           % (i, DECISION_DEAD_MARK, _steps.text(s), label))
            else:
                out.append("  %s %2d. %s"
                           % ("✓" if _steps.is_done(s) else "☐", i, _steps.text(s)))
    # Full decisions log (append-only, uncapped) — the complete why-trail. Unlike the
    # digest this shows REPLACED decisions too, clearly marked and naming exactly what
    # replaced them — superseded by a refutation, SPLIT into atomic parts, or MERGED
    # into a summary. History's job is to stay complete: NO reconcile verb ever deletes
    # a decision, so the record of a wrong turn (and what corrected it) is never lost,
    # and every mark is reversible via `update --restore-decision <n>`. NUMBERED because
    # these 1-based indices are exactly what `--supersedes` / `--pin-decision` take.
    decisions = task.get("decisions") or []
    out.append("")
    out.append("Decisions (%d, oldest first%s):"
               % (len(decisions), _replaced_suffix(decisions)))
    for i, d in enumerate(decisions, 1):
        label = _dec.replacement_label(d)
        if label is not None:
            out.append("  %2d. %s%s  — %s"
                       % (i, DECISION_DEAD_MARK, _dec.text(d), label))
        else:
            out.append("  %2d. %s%s"
                       % (i, DECISION_PIN_MARK if _dec.is_pinned(d) else "", _dec.text(d)))
    if not decisions:
        out.append("  (none recorded)")
    # PRESERVED SUMMARIES — every text a `--summary` replaced, oldest first, NUMBERED
    # because those 1-based positions are exactly what `--restore-summary <n>` takes.
    # Rendered ONLY here: the current summary is what a resume loads, and its ancestors
    # must never cost the digest anything. Data-gated, so a task that has never had a
    # summary replaced renders exactly as it did before this section existed.
    versions = _save.summary_versions(task)
    if versions:
        out.append("")
        out.append("Summary versions (%d preserved, oldest first — `update --task %s "
                   "--restore-summary <n>` brings one back):" % (len(versions), seq))
        for i, v in enumerate(versions, 1):
            out.append("  %2d. [%s] %s"
                       % (i, rel_time(v.get("ts")), v.get("text") or ""))
    # Dated milestone log (`--log` → history) — uncapped. Rendered ONLY here.
    history = task.get("history") or []
    out.append("")
    out.append("Log (%d dated milestone(s), oldest first):" % len(history))
    for e in history:
        when = rel_time(_iso_to_ts(e.get("ts", "")))
        out.append("  • [%s] %s" % (when, e.get("text", "")))
    if not history:
        out.append("  (none recorded)")
    # Full memo ledger — every memo + its complete ack roster, uncapped. Rendered ONLY
    # here and in the default detail's leaner "Memos:" section.
    memos = task.get("memos") or []
    out.append("")
    out.append("Memos (%d, oldest first):" % len(memos))
    for m in memos:
        out.append(_memo_line(m))
    if not memos:
        out.append("  (none recorded)")
    # Full activity log — every entry, uncapped.
    activity = task.get("log") or []
    out.append("")
    out.append("Activity (%d entr%s, oldest first):"
               % (len(activity), "y" if len(activity) == 1 else "ies"))
    for e in activity:
        when = rel_time(_iso_to_ts(e.get("ts", "")))
        out.append("  • [%s] %s" % (when, e.get("note", "")))
    if not activity:
        out.append("  (none recorded)")
    # Full worker-provenance ledger — the complete append-only hub<->worker
    # interaction trail (#463), oldest-first. Data-gated: absent on tasks with no
    # ledger, so a pre-roster task's history renders exactly as before.
    ledger = task.get("ledger") or []
    if ledger:
        hmeta = task.get("session_meta") or {}
        out.append("")
        out.append("Workers (%d interaction(s), oldest first):" % len(ledger))
        for e in ledger:
            who = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                   if e.get("actor_ordinal") is not None
                   else (e.get("actor") or "?")[:8])
            wname = ((hmeta.get(e.get("worker")) or {}).get("name")
                     or (e.get("worker") or "")[:8])
            out.append("  • [%s] %s %s → %s%s" % (
                rel_time(e.get("ts")), who, e.get("action"), wname,
                (" — " + e["detail"]) if e.get("detail") else ""))
    out.append("")
    out.append("(Read-only history view — this did NOT attach or reopen the task. "
               "Resume with /todo %s.)" % seq)
    return "\n".join(out)


def _open_jump_window(cmd):
    """Open a NEW Terminal.app window running `cmd` (the resume one-liner) and
    bring it to the front, via open-session-window.sh. The current window — the
    one /todo was typed in — is left untouched.

    Best-effort and macOS/Terminal.app-only: any failure (not darwin, osascript
    missing, AppleScript error, script absent) returns False so the caller falls
    back to just printing the command for the user to run by hand. Never raises."""
    if sys.platform != "darwin":
        return False
    script = os.path.join(BASE, "open-session-window.sh")
    if not os.path.exists(script):
        return False
    try:
        r = subprocess.run(["bash", script, cmd],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _format_detail_session(task, session, resume=None, opened=False):
    """Compact `/todo <n> -s` view: skip the recap and jump straight into the
    task's main connected working session.

    When `opened` is True we've ALREADY launched a fresh Terminal window running
    `resume` (the current window is left as-is), so we just confirm it. When it's
    False — no recorded session yet, or the auto-open failed — we print the
    one-liner for the user to run by hand. `resume` is the precomputed resume
    command (recomputed here if not supplied)."""
    out = []
    out.append("[SESSION-JUMP] Task [%s] — %s — %s"
               % (task["id"][:8], status_display(task_status(task)).upper(), task["title"]))
    out.append("")
    if resume is None:
        resume = resume_command(task, session)
    fresh = bool(resume) and "--session-id " in resume
    verb = "starting a fresh session for" if fresh else "resuming"
    # The target is a LIVE `--bg` session when the resume one-liner is the bare
    # `claude agents` attach: a `--resume` would be refused, so we ATTACH the exact
    # live session (never a fork copy).
    bg_live = bool(resume) and resume.strip() == "claude agents"
    if bg_live:
        verb = "attaching the live background session for"
    if resume and opened:
        out.append("Opened a NEW Terminal window %s this task's working session "
                   "(this window is left as-is). Command now running there:" % verb)
        out.append("    %s" % resume)
        if bg_live:
            out.append("  (that session is a live background agent — the jump window "
                       "ATTACHES it via `claude agents`; to branch a copy instead: "
                       "`claude --resume <sid> --fork-session`)")
        out.append("")
        out.append("[JUMP-WINDOW-OPENED] The jump window is already running the "
                   "command. Reply with EXACTLY this one line and nothing else (no "
                   "preamble, recap, or extra words); do not run the command yourself:")
        out.append("    ↪ " + task_oneline(task))
    elif resume:
        # Auto-opening the jump window is macOS/Terminal.app-only (_open_jump_window
        # is darwin-gated). Off macOS we degrade to a clear one-liner the user runs
        # by hand — never an error.
        if sys.platform != "darwin":
            out.append("(Opening a jump window is macOS-only — run this in a new terminal:)")
        label = ("Start a fresh session for this task (cd + new session, one command):"
                 if fresh else
                 ("Attach the live background session (`claude agents` → pick this task's "
                  "worker):" if bg_live else
                  "Resume the main connected session (cd + resume, one command):"))
        out.append(label)
        out.append("    %s" % resume)
    else:
        out.append("No recorded working session to resume yet — start one in the "
                   "task's directory, or run `/todo %s` for the full detail."
                   % task.get("seq", task["id"][:8]))
    return "\n".join(out)


def _hub_ordinals(task):
    """Sorted list of the hub ordinals recorded on `task` (backfilling pre-roster
    entries first, so an old task answers too). Used to tell the user which
    `<seq>-<n>` handles actually exist."""
    if ensure_ordinals(task):
        save_task(task)
    return sorted(m["ordinal"] for m in (task.get("session_meta") or {}).values()
                  if m.get("role") == "hub" and m.get("ordinal") is not None)


def _hub_sid_for_ordinal(task, n):
    """The hub session id carrying ordinal `n` on `task`, or None when no hub does.

    Backfills ordinals first so a pre-roster task resolves. Compares `is not None`
    and `==` rather than truthiness — ordinal 0 is a real session."""
    if ensure_ordinals(task):
        save_task(task)
    for sid, m in (task.get("session_meta") or {}).items():
        if m.get("role") == "hub" and m.get("ordinal") is not None and m["ordinal"] == n:
            return sid
    return None


def _ordinal_resume(task, sid):
    """The resume one-liner for the EXACT hub session `sid`, or None when that one
    session can't be resumed (no findable transcript, an empty transcript, or a
    deliberately-skipped session).

    This is the single-target counterpart of `_resume_target`'s heuristic pick: the
    caller has NAMED the session by ordinal, so there is no candidate ranking and no
    fallback to a different session — an unresumable target returns None and the
    caller degrades to the fresh-start form, exactly like `-s` does."""
    m = (task.get("session_meta") or {}).get(sid) or {}
    if get_link(sid) != SKIP_SENTINEL:
        path = _find_session_path(sid)
        if path and _session_msgcount(path) >= 1:
            cwd = _session_cwd(path) or m.get("cwd")
            if cwd:
                return bg_aware_resume(sid, cwd)
    # A session pre-bound but not yet born (`pin --new`) is still a legitimate
    # target: the window that opens BECOMES it. Nothing is minted here.
    if m.get("preborn"):
        return "cd %s && claude --session-id %s" % (m.get("cwd") or os.getcwd(), sid)
    return None


def _jump_ordinal(task, n, session):
    """Jump into the ONE hub session named by `<seq>-<n>` — the ordinal-targeted
    twin of `_jump_one`'s heuristic jump.

    Same contract as `_jump_one`: open a fresh window, leave the INVOKING window
    completely untouched (no attach, no re-tint), and return the `[SESSION-JUMP]`
    block. An unknown ordinal returns a line naming the ordinals that DO exist —
    never a bare "no task matching", which would wrongly suggest the task is gone.
    A recorded-but-unresumable session says so and falls back to the same fresh
    `--session-id` start `-s` uses. Never raises."""
    seq = task.get("seq")
    sid = _hub_sid_for_ordinal(task, n)
    if sid is None:
        known = _hub_ordinals(task)
        if known:
            return ("Task %s has no session -%s. Its sessions are: %s."
                    % (seq, n, ", ".join("%s-%s" % (seq, k) for k in known)))
        return ("Task %s has no numbered hub sessions yet — `/todo %s` opens one."
                % (seq, seq))
    # Log the resumed touch WITHOUT passing the invoking session: linking it would
    # repaint the current window to the jumped task (the v1.9.1 re-tint bug). Only
    # the TARGET session carries this task's colour. Mirrors `_jump_one` exactly.
    touch(task, note="resumed", reopen=True)
    save_task(task)
    # Naming your OWN session by ordinal can't resume it — resuming the very
    # conversation you jumped from is the tainting bug `-s` guards against — so it
    # takes the same fresh-start degrade, just with an accurate reason.
    self_target = bool(session) and sid == session
    resume = None if self_target else _ordinal_resume(task, sid)
    dead = not _is_resumable(resume)
    if dead:
        _sid, resume = fresh_resume_command(task)
    opened = _open_jump_window(resume) if resume else False
    out = _format_detail_session(task, session, resume=resume, opened=opened)
    if dead:
        why = ("is the session you are typing in" if self_target
               else "has no live session to resume (its transcript is gone)")
        out = ("Session %s-%s %s — starting a fresh session for this task instead.\n\n"
               % (seq, n, why)) + out
    return out


def _jump_one(ref, session):
    """Attach `session` to the task named by `ref`, open a fresh jump window for
    it, and return its `[SESSION-JUMP]` block. Used per-ref so `/todo <n,n…> -s`
    can jump into several tasks at once (one window + one block per task).

    A `<seq>-<ordinal>` ref names ONE specific hub session and is routed to
    `_jump_ordinal`; every other ref keeps the heuristic "main working session"
    pick unchanged.

    Returns a no-match line (never raises) so a bad ref in a comma list is
    reported without aborting the others."""
    hit = _parse_ordinal_ref(ref)
    if hit is not None:
        return _jump_ordinal(hit[0], hit[1], session)
    task = resolve_ref(ref)
    if not task:
        return "No task matching '%s'." % ref
    # A `-s` jump opens the task in a NEW window and must leave the INVOKING window
    # completely untouched — no attach, no re-tint. So we log the 'resumed' touch +
    # reopen but DON'T pass (or link) the invoking session: linking it would make
    # cmd_prompt_tint's attached-task fallback repaint the current window to the
    # jumped task (the v1.9.1 bug). Only the TARGET session carries this task's
    # colour — the resumed recorded session (already linked) or the fresh session
    # minted below (fresh_resume_command links THAT sid to the task).
    touch(task, note="resumed", reopen=True)
    save_task(task)
    resume = resume_command(task, session)
    # No concrete session to resume (no recorded one, or the only candidate was
    # THIS session) → mint + pre-bind a fresh one so the jump window auto-attaches
    # to a clean session instead of tainting into the current conversation.
    if not _is_resumable(resume):
        _sid, resume = fresh_resume_command(task)
    opened = _open_jump_window(resume) if resume else False
    return _format_detail_session(task, session, resume=resume, opened=opened)


def _parse_session_flag(arg):
    """Pull a `-s` / `--session` token out of a /todo arg (e.g. `1 -s` or `-s 1`).

    `-s` means "jump straight into the task's connected working session" — emit
    the resume one-liner and skip the recap. Returns (clean_arg, session) where
    clean_arg has the flag removed so it still resolves to the task number/id.
    The flag may sit on either side of the number; only a bare `-s`/`--session`
    token counts, never a substring of an id."""
    toks = (arg or "").split()
    session = False
    kept = []
    for t in toks:
        if t in ("-s", "--session"):
            session = True
        else:
            kept.append(t)
    return " ".join(kept), session


def _is_session_jump_prompt(prompt):
    """True when `prompt` is a `/todo <n> -s` (or `--session`) session-jump.

    The jump opens the task in a NEW window and deliberately leaves the invoking
    session unattached, so cmd_prompt_tint must NOT fall back to repainting the
    current window to the jumped task's colour (the v1.9.1 re-tint bug). Matches
    only a bare `-s`/`--session` token on a todo command — never a substring of
    an id or an arbitrary non-todo prompt that happens to contain `-s`."""
    if not prompt or not cats or not hasattr(cats, "command_name"):
        return False
    name = cats.command_name(prompt)
    if not name or name.split(":")[-1].lower() != "todo":
        return False
    return any(t in ("-s", "--session") for t in prompt.split())


DEFAULT_CLOSED_LIST = 20  # how many closed tasks `/todo closed` (no count) shows


def _parse_list_arg(arg):
    """Recognize the listing keywords `closed [N]` and `all`.

    Returns the closed-task limit to pass to _format_list (None = show every
    closed task) when `arg` is a listing request, or False when it isn't (so
    the caller falls through to treating `arg` as a task ref). `closed` with no
    count uses DEFAULT_CLOSED_LIST; `closed N` uses N; `all` shows everything.
    """
    toks = arg.lower().split()
    if not toks:
        return False
    if toks[0] == "all":
        return None
    if toks[0] in ("closed", "recent"):
        if len(toks) > 1 and toks[1].isdigit():
            return max(1, int(toks[1]))
        return DEFAULT_CLOSED_LIST
    return False


def _print_list_footer():
    """Opt-in (default off) update nudge, list view only. Silent when off/up-to-date."""
    import update_check
    line = update_check.nudge_line()
    if line:
        print(line)


_NO_TASK_ATTACHED = ("No task attached — /todo <n> to open one, or create a task "
                     "first, then /todo save.")


# ---------------------------------------------------------- artifact paths ------
# Deterministic, host-agnostic derivation of a task's brief.html output path under
# the configured artifacts root (config.artifacts_root(), which derives from the
# data_dir seam — never a hardcoded ~/ path). Layout:
#   <artifacts_root>/<project>/<seq>-<title-slug>/brief.html
# <project> comes from the task's active category TAG (its audience), <seq>-<slug>
# from the task number + a filesystem-safe title slug.


def _slug(s):
    """Lowercase, collapse every non-alphanumeric run to a single hyphen, and trim
    leading/trailing hyphens. 'LEGACY Key: Case-Sensitivity!' -> 'legacy-key-case-sensitivity'.
    Empty/None -> ''."""
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _project_slug(task):
    """The project folder for `task`'s brief: the slug of its ACTIVE CATEGORY TAG.

    The tag is the resolved one `/todo` renders as `<emoji> [TAG]`, so the folder
    follows the taxonomy through every layer that shapes it — discipline pack, org
    pack, and a per-slot user override in config.json's `categories` key. Nothing
    about the folder is hardcoded here: a user who renames green's tag gets the
    renamed folder, from THEIR config.

    Reuses categories.hub_slug so a task's artifact folder and its category-hub
    slug (`[[categories/<slug>]]`) stay the same token. Falls back to the colour
    slugified when the colour names no category at all, and to 'general' when the
    task has no colour set."""
    color = (task.get("color") or "").strip().lower()
    if color:
        try:
            import categories as cats
            key = cats.resolve(color)
            if key:
                return cats.hub_slug(key)
        except Exception:
            pass  # categories unavailable: fall back to the colour itself
    return _slug(color) or "general"


def _task_slug(task):
    """`<seq>-<title-slug>` — the per-task artifact folder name. Falls back to the
    8-char id prefix when the task has no stable seq yet."""
    seq = task.get("seq") or task["id"][:8]
    return "%s-%s" % (seq, _slug(task.get("title")) or "task")


def brief_output_path(task):
    """Absolute path a rendered brief is written to:
    <artifacts_root>/<project>/<seq>-<slug>/brief.html. Pure derivation — the caller
    makedirs + writes."""
    import config
    return os.path.join(config.artifacts_root(),
                        _project_slug(task), _task_slug(task), "brief.html")


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


def _resolve_glossary_task(a):
    """The task a glossary command targets: --task <ref> wins, else the session's
    attached task."""
    ref = getattr(a, "task", None)
    if ref:
        return resolve_ref(ref) or load_task(ref)
    return _session_task(getattr(a, "session", None))


def _glossary_mutate(task_id, mutator, session, cap):
    """Run `mutator(task)` under mutate(), setting cap['changed'], and on a real
    change emit task.updated + sync Obsidian. Shared by add/edit/rm."""
    updated = mutate(task_id, mutator)
    if updated is not None and cap.get("changed"):
        _stream_emit("task.updated", updated,
                     _stream_updated_data(updated, ["glossary"]), session)
        _obsidian_sync(updated)
    return updated


def cmd_glossary(a):
    """`task-station glossary [list|add|edit|rm] …` — the per-task canonical
    vocabulary. Resolution: --task <ref> > the --session attached task. A bare
    non-keyword first token (`glossary <task#>`) lists that task's terms.

    Grammar:
      glossary [list]                          list the resolved task's terms
      glossary <task#>                         list another task's terms
      glossary add "<name>" <layer> <state> "<def>"
      glossary edit "<name>" [--layer|--state|--def|--rename …]
      glossary rm "<name>"
    """
    action = (getattr(a, "action", None) or "list").lower()
    args = list(getattr(a, "args", None) or [])
    session = getattr(a, "session", None)
    KEYWORDS = {"list", "add", "edit", "rm"}

    # `glossary <ref>` — list another task's terms (bare non-keyword first token).
    if action not in KEYWORDS:
        task = resolve_ref(a.action) or load_task(a.action)
        if not task:
            print("glossary: unknown action or task %r — use list/add/edit/rm, "
                  "or a task number." % a.action)
            return
        print(_format_glossary(task))
        return

    task = _resolve_glossary_task(a)
    if not task:
        print("glossary: no task — attach a session or pass --task <ref>.")
        return
    tid = task["id"]
    seq = task.get("seq", tid[:8])

    if action == "list":
        print(_format_glossary(task))
        return

    name = args[0] if args else None
    if not name:
        print('glossary %s: give a term name, e.g. glossary %s "<name>" …' % (action, action))
        return

    if action == "add":
        layer = a.layer if a.layer is not None else (args[1] if len(args) > 1 else "")
        state = a.state if a.state is not None else (args[2] if len(args) > 2 else "")
        definition = a.definition if a.definition is not None else (args[3] if len(args) > 3 else "")
        cap = {}

        def _apply(t):
            cap["changed"] = add_glossary_term(t, name, layer, state, definition)
            if cap["changed"]:
                touch(t, session=session, note="glossary +%s" % name, register=False)
        updated = _glossary_mutate(tid, _apply, session, cap)
        print(_format_glossary(updated or task))
        return

    if action == "edit":
        cap = {}

        def _apply(t):
            r = edit_glossary_term(t, name, layer=a.layer, state=a.state,
                                   definition=a.definition, rename=a.rename)
            cap["result"] = r
            cap["changed"] = bool(r)
            if r:
                touch(t, session=session, note="glossary ~%s" % name, register=False)
        try:
            updated = _glossary_mutate(tid, _apply, session, cap)
        except ValueError as e:
            print("glossary edit: %s" % e)
            return
        if cap.get("result") is None:
            print("glossary edit: no term named %r on task #%s." % (name, seq))
            return
        print(_format_glossary(updated or task))
        return

    if action == "rm":
        cap = {}

        def _apply(t):
            cap["changed"] = remove_glossary_term(t, name)
            if cap["changed"]:
                touch(t, session=session, note="glossary -%s" % name, register=False)
        updated = _glossary_mutate(tid, _apply, session, cap)
        if not cap.get("changed"):
            print("glossary rm: no term named %r on task #%s." % (name, seq))
            return
        print("Removed '%s' from task #%s glossary." % (name, seq))
        return


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


def cmd_glossary_context(a):
    """`task-station glossary-context [--task|--session]` — print the injectable
    glossary block for a task (the adapter hook non-Claude hosts emit through their
    own prompt channel). Silent when the task has no terms."""
    task = _resolve_glossary_task(a)
    if not task:
        return
    block = glossary_context(task)
    if block:
        print(block)


# ---------------------------------------------------------------- brief ---------

def _brief_provenance_sessions(task):
    """Roster rows for the brief's Sessions table (#463), derived from session_meta:
    hubs first (by ordinal) then workers (newest spawned first). [] when the task has
    no sessions — so the brief's provenance section stays absent (data-gated)."""
    meta = task.get("session_meta") or {}
    if not meta:
        return []
    ensure_ordinals(task)
    def _key(kv):
        _sid, m = kv
        if m.get("role") == "hub":
            o = m.get("ordinal")
            return (0, o if o is not None else float("inf"))
        return (1, -(m.get("spawned_at") or m.get("ts") or 0))
    rows = []
    for sid, m in sorted(meta.items(), key=_key):
        role = m.get("role") or "unknown"
        if role == "hub":
            rows.append({"ordinal": ordinal_label(task, sid) or "",
                         "kind": "hub", "name": sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
        else:
            rows.append({"ordinal": "", "kind": role,
                         "name": m.get("name") or sid[:8], "model": m.get("model") or "",
                         "status": m.get("status") or "",
                         "spawned": rel_time(m.get("spawned_at") or m.get("ts"))})
    return rows


def _brief_provenance_ledger(task, limit=5):
    """The last `limit` hub<->worker interactions for the brief (#463), oldest→newest.
    [] when the task has no ledger (data-gated)."""
    led = task.get("ledger") or []
    if not led:
        return []
    meta = task.get("session_meta") or {}
    out = []
    for e in led[-limit:]:
        actor = ("%s-%s" % (task.get("seq"), e["actor_ordinal"])
                 if e.get("actor_ordinal") is not None
                 else (e.get("actor") or "?")[:8])
        worker = ((meta.get(e.get("worker")) or {}).get("name")
                  or (e.get("worker") or "")[:8])
        out.append({"when": rel_time(e.get("ts")), "actor": actor,
                    "action": e.get("action"), "worker": worker,
                    "detail": e.get("detail")})
    return out


def _brief_persist_path(task, out, session):
    """Persist task['brief_path'] = out through mutate(), then emit task.updated and
    sync Obsidian. Shared by the `render` and `path` actions so a brief is findable
    the same way however it was produced — the contract-v2 note frontmatter carries
    brief_path automatically once it is on the record."""
    def _apply(t):
        t["brief_path"] = out
    updated = mutate(task["id"], _apply)
    if updated is not None:
        _stream_emit("task.updated", updated,
                     _stream_updated_data(updated, ["brief_path"]), session)
        _obsidian_sync(updated)


def cmd_brief(a):
    """`task-station brief [render|path] [--task|--session] [--spec FILE]`.

    **path** — resolve the task, create the artifact dir, persist and print
    brief_output_path(task). Reads no spec. This is the model-authored flow: the
    `/brief` skill asks for the path, then writes its own HTML there.

    **render** (default, retained for back-compat) — read a brief-spec (JSON) from
    --spec FILE or stdin, lazy-import lib/brief (pure stdlib, host-agnostic), render
    it against the task's glossary into the frozen house-style template, write to
    brief_output_path (makedirs), persist task['brief_path'] and print the path."""
    task = _resolve_glossary_task(a)
    if not task:
        print("brief: no task — attach a session or pass --task <ref>.")
        return

    session = getattr(a, "session", None)

    if (getattr(a, "action", None) or "").strip().lower() == "path":
        out = brief_output_path(task)
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
        except OSError as e:
            print("brief: cannot create %s: %s" % (os.path.dirname(out), e))
            return
        _brief_persist_path(task, out, session)
        print(out)
        return

    src = getattr(a, "spec", None)
    try:
        if src:
            with open(os.path.expanduser(src), encoding="utf-8") as f:
                raw = f.read()
        else:
            raw = sys.stdin.read()
    except OSError as e:
        print("brief: cannot read spec: %s" % e)
        return
    raw = (raw or "").strip()
    if not raw:
        print("brief: empty spec — pass --spec FILE or pipe the brief-spec JSON on stdin.")
        return
    try:
        spec = json.loads(raw)
    except ValueError as e:
        print("brief: spec is not valid JSON: %s" % e)
        return

    import brief as _brief   # lazy: keep the renderer off the hot engine paths
    glossary = _normalize_glossary(task.get("glossary"))
    # Inject the task's session roster + worker ledger tail (#463) unless the spec
    # already supplies them. Data-gated in the renderer: empty lists → no section.
    if isinstance(spec, dict):
        spec.setdefault("sessions", _brief_provenance_sessions(task))
        spec.setdefault("ledger", _brief_provenance_ledger(task))
    html = _brief.render_brief(spec, glossary)

    out = brief_output_path(task)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        print("brief: cannot write %s: %s" % (out, e))
        return

    _brief_persist_path(task, out, session)
    print(out)


def cmd_recap(a):
    """`task-station recap [--week YYYY-Www] [--open] [--json] [--no-scan]
    [--auto-if-due]` — build the strictly-private weekly usage recap: a local,
    self-contained HTML one-pager under <data_dir>/recaps/<week>.html summarizing what
    you did, what it cost, and concrete guidance to use LLMs more effectively.

    Reads only the persisted ledger + task store (no transcript IO of its own); by
    default it first runs an incremental scan-all so the week's numbers are current
    (--no-scan skips it). --auto-if-due is the hook entry point: it self-gates on the
    `recap` config toggle + a once-per-week stamp and is a silent, fail-open no-op
    otherwise. Output is machine-local and added to NO sync boundary."""
    import config
    import recap as _recap
    store = _backend()

    if getattr(a, "auto_if_due", False):
        # Hook path: strictly gated + silent. auto_generate_if_due swallows all errors.
        path = _recap.auto_generate_if_due(store)
        if path and not getattr(a, "quiet", False):
            print(path)
        return

    if not config.usage_tracking_enabled():
        print("recap: usage tracking is off (config --usage-tracking off) — no data "
              "to summarize.")
        return

    if not getattr(a, "no_scan", False):
        try:
            _usage_engine().scan_all(store)      # freshen the ledger; best-effort
        except Exception:
            pass                                  # stale numbers beat a crashed recap

    try:
        result = _recap.generate(store, week=getattr(a, "week", None))
    except ValueError as e:
        print("recap: %s" % e)
        return

    if getattr(a, "as_json", False):
        print(json.dumps(result["aggregates"], ensure_ascii=False, indent=2))
        return

    print(result["path"])
    if getattr(a, "open", False):
        _open_path(result["path"])


def _session_task(session):
    """The task attached to `session`, or None (skipped/unattached both read None)."""
    link = get_link(session) if session else None
    if not link or link == SKIP_SENTINEL:
        return None
    return load_task(link)


def _save_flags(rest):
    """The two flags `/todo save` takes, read WITHOUT argparse: `(verbose, check)`.

    Deliberately a token scan rather than a parser. `/save` has always ignored trailing
    free text — `/todo SAVE please checkpoint this` is a documented, tested shape — and
    an argparse spec would turn that into a usage error. `rest` also arrives as a list
    from a couple of internal callers, so both shapes are accepted."""
    raw = rest if isinstance(rest, str) else " ".join(rest or [])
    toks = raw.split()
    return ("--verbose" in toks), ("--check" in toks)


def _save_check_block(task, seq, session):
    """`/todo save --check` — the MECHANICAL cold-read verification, and NOTHING else.

    The gap report alone: no capture checklist, no command templates, no digest. It is
    what step 6 of the save flow runs after a write to prove the gaps actually closed,
    and it is READ-ONLY — it stamps nothing, clears no flag and writes no session
    record. That is the same contract `heal --scan` keeps, and for the same reason: a
    verification pass that mutates what it verifies cannot be trusted about either."""
    report = _save.gap_report(task, digest_chars=len(_format_detail(task, session)))
    out = ["[SAVE] Task #%s [%s] — session %s"
           % (seq, task["id"][:8], (session or "")[:8]),
           "COLD-READ CHECK — the gap report, re-run. This is READ-ONLY: nothing was "
           "changed, no checkpoint was stamped."]
    out.extend(_save.gap_lines(report))
    out.append("")
    out.append("VERDICT: %s"
               % ("PASS — every named slot carries something and the state leads with "
                  "`NEXT:`" if _save.is_clean(report)
                  else "FAIL — patch each line above with another `update --task %s …`, "
                       "then re-run `/todo save --check`" % seq))
    return "\n".join(out)


def _todo_save(a, rest):
    """/todo save — checkpoint the CURRENT task's context into its digest so a
    FRESH session can resume with zero context loss. Prints a model-facing [SAVE]
    block: a GAP REPORT of what the digest is MISSING + the named-slot capture
    checklist + the exact `update` templates + the mechanical cold-read check.
    Records THIS session (with its cwd) as a transcript backstop for `/todo <n> -s`.
    Mints NO session and performs NO pin — capture only.

    IT DOES NOT DUMP THE DIGEST, and it does NOT STAMP. Both are deliberate reversals:

      * The caller has been working this task all along, so it already HAS the digest.
        Measured on one real task, echoing it back cost 71,516 characters of which
        71,271 — 99.7% — were the dump. What the caller does not have is the list of
        what is missing, which is what `save.gap_report` computes. `--verbose` still
        prints the full digest; `--check` prints the gap report alone, which is the
        mechanical cold-read re-check after a write.
      * `last_full_save_ts` claims a full checkpoint was CAPTURED. Emitting a prompt
        captures nothing, so this records only that a save was STARTED; the stamp
        belongs to the `update` that writes a summary AND a state (see
        `save.is_checkpoint_write`)."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    verbose, check_only = _save_flags(rest)
    seq = task.get("seq", task["id"][:8])
    if check_only:
        # `--check` is READ-ONLY, and that is its whole contract — the same one
        # `heal --scan` keeps. It runs BEFORE any of the writes below precisely because
        # a verification pass that mutates the thing it is verifying is the bug this
        # release exists to fix, one surface over: it would print "nothing was changed"
        # having just changed something.
        print(_save_check_block(task, seq, a.session))
        return
    # A save has been STARTED — that, and only that, is what emitting the block proves.
    # The two staleness flags still clear here, and they are a different kind of claim:
    # they gate NUDGES ("your digest looks stale", "run /todo save NOW"), and the nudge
    # has been delivered and acted on the moment this block is read. The checkpoint
    # STAMP is a claim about captured content, so it waits for the write.
    _save.mark_save_started(task)
    clear_digest_dirty(task)
    clear_pressure_nudged(task)
    save_task(task)
    # Notifications that a save HAPPENED (note + daily-note entry + feed event) — the
    # record that a checkpoint was CAPTURED is `last_full_save_ts`, and that one waits
    # for the write. These fire here because the export and the feed track activity,
    # not the stamp.
    _obsidian_event(task, "checkpoint")
    _stream_emit("task.checkpoint", task, _stream_digest(task), a.session)
    # Transcript backstop: make sure THIS session is a findable resume candidate so a
    # later fresh session can `/todo <n> -s` back into the FULL transcript if the digest
    # ever misses a detail. Record its cwd in session_meta (authoritative from the
    # transcript when available; os.getcwd() fallback) WITHOUT pinning. Only writes when
    # the entry is missing or lacks a cwd, so an existing record is left intact.
    if a.session:
        meta = task.setdefault("session_meta", {})
        entry = meta.get(a.session)
        if not entry or not entry.get("cwd"):
            path = _find_session_path(a.session)
            cwd = (_session_cwd(path) if path else None) or os.getcwd()
            meta[a.session] = {"cwd": cwd, "ts": _now(), "role": "hub"}
            save_task(task)
    # The digest is rendered ONCE and, by default, only MEASURED — its length is the
    # "what a fresh session loads" number in the gap report. Rendering it costs the same
    # as it always did; what changed is that the 71,271 characters no longer go to the
    # caller, who has been working this task and already has them.
    detail = _format_detail(task, a.session)
    report = _save.gap_report(task, digest_chars=len(detail))
    out = []
    out.append("[SAVE] Task #%s [%s] — session %s"
               % (seq, task["id"][:8], (a.session or "")[:8]))
    out.append("Checkpoint this task so a FRESH session — with NO memory of this "
               "conversation — can resume with ZERO context loss. AMEND what the GAP "
               "REPORT names via the `update` command; do NOT rewrite slots that are "
               "already accurate — a save is an amendment, not a rewrite.")
    # THE SAVE GATE. `--summary` REPLACES the summary wholesale, so writing one from a
    # decision set that still contains refuted entries bakes the drift into the very
    # first field anyone reads. One line, and it does NOT block and does NOT run the
    # heal — this is a warning at a decision point, not a gate. Fail-open.
    try:
        gate = _heal.gate_line(task)
    except Exception:
        gate = None
    if gate:
        out.append("[task-station] %s The summary you are about to REPLACE would be "
                   "written from a decision set that has not been reconciled." % gate)
    out.append("")
    # THE GAP REPORT REPLACED THE DIGEST DUMP. See `_todo_save`'s docstring for the
    # measurement; the short version is that echoing the digest back to the session that
    # wrote it was 99.7% of this block's cost and told it nothing it did not know.
    out.append("GAP REPORT — what the digest is MISSING. The digest itself is NOT "
               "reprinted: you have been working this task, so you already have it "
               "(`/todo save --verbose` dumps it if you genuinely do not).")
    out.extend(_save.gap_lines(report))
    if verbose:
        out.append("")
        out.append("CURRENT DIGEST (--verbose)")
        out.append(detail)
    out.append("")
    out.append("CAPTURE CHECKLIST — the reference for each slot. Write ONLY the ones the "
               "GAP REPORT named; leave an accurate slot alone. Fill EVERY slot you do "
               "write with SPECIFICS (exact paths, names, values, commands — never vague "
               "summaries):")
    out.append("  1. GOAL (--goal): the objective — what \"done\" concretely looks like.")
    out.append("  2. NEXT ACTION (--state): the state line MUST LEAD with "
               "`NEXT: <the concrete first move the resumed session should make>`, then "
               "the current standing. Specific enough to act on immediately.")
    out.append("  3. STEPS (--step-add / --step-done N): the FULL plan as a checklist, "
               "marking done vs not-done accurately — INCLUDE the not-yet-started steps.")
    out.append("     • A step gone STALE (the plan moved on, or it names something "
               "retired) is retired with `--step-supersede <n>` — add the corrected step "
               "in the same call and it is recorded as the replacement. Do NOT tick it "
               "done (nobody did it) and do NOT add a warning step about it. It leaves "
               "the checklist and both sides of the n/m count, stays in `/todo %s "
               "history`, and `--step-restore <n>` undoes it." % seq)
    out.append("  4. DECISIONS + WHY (--decision, one per): every material choice AND its "
               "rationale — INCLUDING approaches TRIED and REJECTED and why, so the resume "
               "never re-explores dead ends.")
    out.append("     • REPLACING an earlier call? Add the new decision with "
               "`--supersedes <n>` (the number from `/todo %s history`, repeatable). The "
               "old one then vanishes from this digest instead of sitting here "
               "contradicting the new one — a refuted decision left visible is worse "
               "than no decision." % seq)
    out.append("     • Nothing is hidden by age: EVERY still-current decision renders in "
               "this digest, however old and however many. A decision leaves the digest "
               "only by ceasing to be true (`--supersedes`, or `heal`'s split/merge).")
    out.append("     • ARCHITECTURE SPINE — a rule the rest of the work must obey? Add it "
               "with `--pin` (or `--pin-decision <n>`). A pin is READING ORDER, not "
               "visibility: pinned decisions sort FIRST (marked ★), then everything else "
               "oldest-first. No limit; keep the pinned set to the spine so leading with "
               "it still means something.")
    out.append("     • ONE decision per --decision, atomic. Past %d chars you get an "
               "advisory suggesting `heal --split` — it is a SUGGESTION and the write "
               "always succeeds in full, so never drop a fact or fake two entries out of "
               "one to get under it." % _dec.LONG_DECISION_CHARS)
    out.append("  5. CONTEXT SNAPSHOT (--summary, REPLACE): rewrite `summary` to the CURRENT "
               "truth — a lean structured snapshot (this REPLACES the summary wholesale; keep "
               "it the present state, NOT a running log — do NOT dump the history into it, the "
               "why-trail lives in decisions + --log, read it back via `/todo <n> history`). It "
               "must EXPLICITLY cover —")
    out.append("       • Files / modules touched or relevant, with PATHS, and how they "
               "fit together.")
    out.append("       • The repo / branch / worktree / environment + any auth / config / "
               "tooling quirks.")
    out.append("       • Commands to build / test / run / reproduce.")
    out.append("       • Constraints & gotchas — \"watch out for X\", \"never do Y\".")
    out.append("       • Open questions / blockers awaiting a decision.")
    out.append("       • The user's most recent intent (what they last asked for), in "
               "their words.")
    out.append("     • The summary you replace is NOT destroyed: it is kept, append-only, "
               "and `update --task %s --restore-summary` puts the previous one back "
               "(`--restore-summary <n>` for an older version; `/todo %s history` lists "
               "them). A thin save can no longer silently lose a good summary." % (seq, seq))
    out.append("  5b. LOG (--log): one dated line for a milestone/finding worth keeping in "
               "history (does not load on normal resume). Exactly ONE per save.")
    out.append("  6. LINKS (--pr / --story): PRs and work-items.")
    out.append("")
    out.append("Command templates (seq %s filled in — one call or several):" % seq)
    out.append("    task-station update --task %s \\" % seq)
    out.append("      --goal '<what done looks like>' \\")
    out.append("      --state 'NEXT: <concrete first move> — <current standing>' \\")
    out.append("      --step-add '<not-yet-started step>' --step-done <N> \\")
    out.append("      --decision '<decision + why — incl. what was tried & rejected>' \\")
    out.append("      --log '<vX.Y.Z shipped: what — or a finding worth keeping in history>' \\")
    out.append("      --pr '<url>' --story '<url>' \\")
    out.append("      --summary '<CURRENT snapshot: files+paths · branch/env · commands · "
               "gotchas · open questions · user's latest intent>'")
    out.append("    (--summary REPLACES the summary wholesale — use it for the current "
               "snapshot; --append-summary only adds. History goes to --decision / --log, "
               "not into --summary.)")
    out.append("    " + _cli_fallback())
    out.append("")
    # THE STAMP BELONGS TO THE WRITE, and the block has to say so — otherwise the next
    # reader assumes running `/save` was the checkpoint, which is precisely the belief
    # that let an empty summary sit under a `last full save just now`.
    out.append("THIS BLOCK DID NOT STAMP A CHECKPOINT. `last_full_save_ts` means \"a full "
               "checkpoint was CAPTURED\", and printing a prompt captures nothing — all "
               "this recorded is that a save was STARTED. The stamp lands on the `update` "
               "that writes a `--summary` AND a `--state` together, because that pair IS "
               "the checkpoint; no flag declares it, so no one can claim one without "
               "writing it.")
    out.append("")
    out.append("COLD-READ CHECK — after the write, and MECHANICAL, not a feeling: every "
               "named slot must be non-empty and `state` must begin with `NEXT:`. The "
               "stamping `update` reports any that still fail; `/todo save --check` "
               "re-runs the same check on demand. Then the judgement half: re-read the "
               "digest as if you have NO memory of this conversation and PATCH anything "
               "ambiguous or assumed with another `update`.")
    out.append("")
    out.append("Not pinned — /todo save only captures. DO NOT pin a session or open / "
               "resume anything. If a detail is ever missing, the trail is recoverable: "
               "`/todo %s history` (the decisions + log record) or `/todo %s -s` "
               "(this session's full transcript)." % (seq, seq))
    print("\n".join(out))


def _todo_pin(a, rest):
    """/todo pin — pin THIS session as the attached task's resume target (same as
    the standalone /pin). No --new: pins the current session."""
    task = _session_task(a.session)
    if not task:
        print(_NO_TASK_ATTACHED)
        return
    print(_pin_one(str(task.get("seq") or task["id"]), a))


def _todo_unpin(a, rest):
    """/todo unpin [n,…] — drop the pinned resume session. With a numeric list,
    unpins those task(s); bare, unpins THIS session's attached task (inverse of
    /todo pin). Reuses cmd_unpin."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_unpin(ns)


def _todo_done(a, rest):
    """/todo done [n,…] — close the current session's attached task, or the
    task(s) named by number. Reuses cmd_done (does NOT close the terminal window —
    you're mid-session; that's intended)."""
    ns = argparse.Namespace(session=a.session, task=(rest or None))
    cmd_done(ns)


def _todo_config(a, rest):
    """/todo config [flags] — route to the config console. Everything after the
    keyword is tokenized (shlex) and parsed by the same argparse spec cmd_config
    uses, then dispatched in-process so config prints verbatim."""
    import shlex
    import config
    parser = argparse.ArgumentParser(prog="/todo config", add_help=False)
    _add_config_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest))
    except SystemExit:
        return   # argparse already reported the bad flag/usage
    config.cmd_config(ns)


def _todo_search(a, rest):
    """/todo search [<--open|--closed|--all>] <terms> — the search surface on the
    /todo command (mirrors the standalone `search` subcommand's tier-1 output).
    Defined here (above _TODO_SUBCMDS) because that dict literal references it at
    module-load; the _search_core/_format_search it calls resolve at runtime."""
    rest = (rest or "").strip()
    want = "all"
    m = re.match(r"^--(open|closed|all)\b\s*(.*)$", rest)
    if m:
        want, rest = m.group(1), m.group(2).strip()
    if not rest:
        print("search: give one or more terms, e.g. /todo search auth token")
        return
    detail = _numeric_ref_detail(rest, a.session)
    if detail is not None:
        print(detail)
        return
    print(_format_search(rest, _search_core(rest, want), want))


# Reserved /todo leading keywords → handler(a, rest). Checked before the numeric/
# ref parsing; each triggers only on the exact leading token (case-insensitive).
def _todo_native(a, rest):
    """/todo native — read-only listing of Claude Code's recent native task lists."""
    print(_format_native())


def _todo_adopt(a, rest):
    """/todo adopt <list-prefix>:<id> — promote a native task into a durable station
    task (read-only on the native side)."""
    cmd_adopt(argparse.Namespace(native=(rest or "").strip() or None))


def _memo_ns(**kw):
    """An argparse.Namespace for cmd_memo with every optional field defaulted, so the
    slash surface never trips a getattr on a flag it doesn't spell."""
    ns = dict(sub=None, task=None, id=None, text=None, session=None,
              decision=None, memory=None, noop=None, corrects=None)
    ns.update(kw)
    return argparse.Namespace(**ns)


def _todo_memo(a, rest):
    """/todo memo — hand a fact/decision to a task's session(s). Grammar:
        /todo memo <n> <text…>        send to task n FROM this session
        /todo memo ack <id8> <TEXT>   ack, promoting the memo to a decision
        /todo memo ack <id8> memory:<slug>   ack, folded into that memory note
        /todo memo ack <id8> noop:<reason>   ack, no durable change needed
        /todo memo show [<n>] [<id8>] list the attached/nth task's memos, or one full body
    An ack MUST carry one of the three dispositions — a bare `/todo memo ack <id8>` is
    an error naming all three. Routes to cmd_memo so the CLI + slash surfaces share one
    code path."""
    toks = (rest or "").split()
    if not toks:
        cmd_memo(_memo_ns(sub="show", session=a.session))
        return
    head = toks[0].lower()
    if head == "ack":
        mid = toks[1] if len(toks) > 1 else None
        # Everything after the id8 is the disposition. `memory:` / `noop:` select the two
        # non-decision dispositions; anything else is the promote-to-decision text.
        text = rest.split(None, 2)[2].strip() if len(toks) > 2 else ""
        kw = {}
        low = text.lower()
        if low.startswith("memory:"):
            kw["memory"] = text[len("memory:"):].strip()
        elif low.startswith("noop:"):
            kw["noop"] = text[len("noop:"):].strip()
        elif text:
            kw["decision"] = text
        cmd_memo(_memo_ns(sub="ack", id=mid, session=a.session, **kw))
        return
    if head == "show":
        rest_toks = toks[1:]
        task_ref = None
        mid = None
        for tk in rest_toks:
            if tk.isdigit() and task_ref is None:
                task_ref = tk
            else:
                mid = tk
        cmd_memo(_memo_ns(sub="show", task=task_ref, id=mid, session=a.session))
        return
    # Default: send — first token is the target task number, the remainder is the body.
    ref = toks[0]
    body = rest[len(toks[0]):].strip()
    cmd_memo(_memo_ns(sub="send", task=ref, text=body, session=a.session))


def _todo_glossary(a, rest):
    """/todo glossary [flags] — route to the glossary console. Everything after the
    keyword is tokenized (shlex) and parsed by the SAME argparse spec cmd_glossary
    uses; this session is injected as --session so task resolution matches /glossary."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo glossary", add_help=False)
    _add_glossary_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session      # the /todo dispatch owns the session, not `rest`
    cmd_glossary(ns)


def _todo_brief(a, rest):
    """/todo brief [flags] — route to the brief renderer (parity with /brief).
    Tokenized + parsed by the SAME spec cmd_brief uses; this session is injected."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo brief", add_help=False)
    _add_brief_args(parser)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return
    ns.session = a.session
    cmd_brief(ns)


def _todo_heal(a, rest):
    """/todo heal [--scan|--apply|--all|<n>] — the reconcile pass on the current task
    (or the one named). Everything after the keyword is tokenized and parsed by the same
    spec `cmd_heal` uses, so `/todo heal --scan` and `task-station heal --scan` behave
    identically. A bare `/todo heal` is a DRY RUN and changes nothing."""
    import shlex
    parser = argparse.ArgumentParser(prog="/todo heal", add_help=False)
    parser.add_argument("ref", nargs="?", default=None)
    parser.add_argument("--task", default=None)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--all", dest="all", action="store_true")
    parser.add_argument("--split", type=int, default=None)
    parser.add_argument("--merge", default=None)
    parser.add_argument("--into", default=None)
    parser.add_argument("--mark-healed", dest="mark_healed", action="store_true")
    parser.add_argument("--note", default=None)
    parser.add_argument("--dispose-acks", dest="dispose_acks", default=None)
    parser.add_argument("--decision", nargs="?", const=True, default=None)
    parser.add_argument("--memory", default=None)
    parser.add_argument("--noop", default=None)
    try:
        ns = parser.parse_args(shlex.split(rest or ""))
    except SystemExit:
        return                       # argparse already reported the bad flag/usage
    ns.session = a.session
    # A bare leading number is the task ref (`/todo heal 12`), matching how the other
    # /todo subcommands take one. The FOLD ITSELF belongs to `cmd_heal`
    # (`_heal_positional_ref`), which the top-level `heal` subparser reaches too — one
    # place deciding what a positional means, and one place refusing the combinations it
    # cannot mean. A second precedence rule here is how two surfaces start disagreeing
    # about which task a command was aimed at.
    cmd_heal(ns)


_TODO_SUBCMDS = {
    "save": _todo_save,
    "heal": _todo_heal,
    "pin": _todo_pin,
    "unpin": _todo_unpin,
    "done": _todo_done,
    "config": _todo_config,
    "native": _todo_native,
    "adopt": _todo_adopt,
    "search": _todo_search,
    "memo": _todo_memo,
    "glossary": _todo_glossary,
    "brief": _todo_brief,
}


def _status_dot(task):
    """The board glyph for a task: ○ open · ● active · ✕ closed — for the search
    hit list (mirrors the detail/board glyphs)."""
    cur = task_status(task)
    if cur == STATUS_CLOSED:
        return STATUS_GLYPH_CLOSED
    return STATUS_GLYPH.get(cur, STATUS_GLYPH[STATUS_OPEN])


def _search_snippet_clean(s):
    """Whitespace-collapse a snippet to a single line (FTS5 fragments can span the
    multi-line content blob)."""
    return re.sub(r"\s+", " ", s or "").strip()


def _search_core(query, want="all", limit_shown=SEARCH_HITS_SHOWN):
    """Run the ranked search, load each hit, apply the status filter, and keep the
    top `limit_shown`. Returns [(task, snippet), …] in rank order. `want` is
    all|open|closed (open = the board: open + active)."""
    rows = []
    for h in search_tasks(query, limit=SEARCH_SCAN_LIMIT):
        task = load_task(h["id"])
        if not task:
            continue
        if want == "open" and is_closed(task):
            continue
        if want == "closed" and not is_closed(task):
            continue
        rows.append((task, h.get("snippet") or ""))
        if len(rows) >= limit_shown:
            break
    return rows


def _format_search(query, rows, want):
    """Tier-1 search output: a token-economical ranked hit list — `#seq <dot> title`
    plus a one-line match-context snippet each — under a summary header, with a note
    pointing at the digest / full-trail commands. Empty-result case says so plainly."""
    ensure_seqs()   # so every hit has a stable #seq to show / address by
    label = {"all": "all tasks", "open": "open + active tasks",
             "closed": "closed tasks"}.get(want, "all tasks")
    if not rows:
        return ("No %s match '%s'.\n"
                "Try fewer/broader terms, or --all to include closed tasks."
                % (label, query))
    out = ["Search '%s' — %d hit%s (%s), most relevant first:"
           % (query, len(rows), "" if len(rows) == 1 else "s", label)]
    for task, snippet in rows:
        seq = task.get("seq", task["id"][:8])
        out.append("  #%s %s %s" % (seq, _status_dot(task), task.get("title", "")))
        snip = _search_snippet_clean(snippet)
        if snip:
            out.append("      %s" % snip)
    out.append("")
    out.append("task-station search --detail <seq> for the digest; "
               "/todo <n> history for the full trail.")
    return "\n".join(out)


def _numeric_ref_detail(query, session):
    """A bare all-digit search query (e.g. `search 362`) is a lookup by the task's
    display number (#seq), not a text search — resolve it to that task's read-only
    digest so a numeric lookup never falsely reports "no match". Returns the
    formatted detail string, or None to fall through to text search (query isn't a
    lone number, or no task carries that number — e.g. a PR/story number)."""
    q = (query or "").strip()
    if not q.isdigit():
        return None
    task = resolve_ref(q)
    if not task:
        return None
    return _format_detail(task, session, attached=False)


def cmd_search(a):
    """`task-station search <terms>` — ranked cross-task search (tier 1), or
    `--detail <seq>` to print one task's full read-only digest."""
    if getattr(a, "detail", None):
        task = resolve_ref(a.detail) or load_task(a.detail)
        if not task:
            print("No task matching '%s'." % a.detail)
            return
        print(_format_detail(task, a.session, attached=False))
        return
    query = " ".join(a.terms).strip()
    if not query:
        print("search: give one or more terms, e.g. task-station search auth token")
        return
    detail = _numeric_ref_detail(query, a.session)
    if detail is not None:
        print(detail)
        return
    want = "open" if getattr(a, "open", False) else \
           "closed" if getattr(a, "closed", False) else "all"
    print(_format_search(query, _search_core(query, want), want))


def cmd_render(a):
    # --format md makes the LIST branches emit GitHub-flavored Markdown tables the
    # skill prints verbatim (no hand-transcription). Detail and session-jump
    # branches are unaffected — they stay ASCII for this PR.
    md = getattr(a, "format", None) == "md"
    _fmt_list = _format_list_md if md else _format_list
    # Reserved leading keywords — save · pin · done · config — route the existing
    # actions through /todo. Checked BEFORE the -s/numeric/ref parsing so they
    # trigger ONLY on the exact leading token (a task never takes a free-text
    # title, so there's no collision with the numeric/board/closed/all path).
    raw = (a.arg or "").strip()
    toks = raw.split()
    if toks and toks[0].lower() in _TODO_SUBCMDS:
        kw = toks[0].lower()
        rest = raw[len(toks[0]):].strip()   # everything after the leading keyword
        _TODO_SUBCMDS[kw](a, rest)
        return
    # /todo <n> history (also `history <n>`) — the on-demand FULL trace: the
    # complete decisions log + the dated milestone log + the full activity log.
    # READ-ONLY: unlike `/todo <n>`, it does NOT attach, reopen, or mutate the
    # task, and it is the only view that renders the milestone log — so that
    # ever-growing trail stays OFF the default resume path. Checked before the
    # -s/numeric/ref parsing (two tokens, one of them the literal `history`).
    if len(toks) == 1 and toks[0].lower() == "history":
        # Bare `history` (no number, e.g. /task-station:history with no args) —
        # the CURRENT session's attached task's full trace. Same read-only
        # rendering as `<n> history` below, resolved via get_link instead of a ref.
        task = _session_task(a.session)
        if not task:
            print("No task attached — /todo <n> history for a specific task.")
            return
        print(_format_history(task))
        return
    if len(toks) == 2 and any(t.lower() == "history" for t in toks):
        ref = toks[1] if toks[0].lower() == "history" else toks[0]
        task = resolve_ref(ref)
        if not task:
            print("No task matching '%s'.\n\n%s" % (ref, _fmt_list()))
            return
        print(_format_history(task))
        return
    # /todo <n> prompts (also `prompts <n>`) — the WS6 read-only prompt trail: the
    # chronological, session-attributed list of the exact user prompts that drove
    # this task. READ-ONLY like `history` — it does NOT attach, reopen, or mutate.
    if len(toks) == 1 and toks[0].lower() == "prompts":
        task = _session_task(a.session)
        if not task:
            print("No task attached — /todo <n> prompts for a specific task.")
            return
        print(_format_prompts_view(task))
        return
    if len(toks) == 2 and any(t.lower() == "prompts" for t in toks):
        ref = toks[1] if toks[0].lower() == "prompts" else toks[0]
        task = resolve_ref(ref)
        if not task:
            print("No task matching '%s'.\n\n%s" % (ref, _fmt_list()))
            return
        print(_format_prompts_view(task))
        return
    arg, jump = _parse_session_flag(raw)
    if not arg:
        print(_fmt_list())
        _print_list_footer()
        return
    closed_limit = _parse_list_arg(arg)
    if closed_limit is not False:
        print(_fmt_list(closed_limit=closed_limit))
        _print_list_footer()
        return
    # `<seq>-<ordinal>` names ONE specific hub session and IMPLIES a jump, so
    # `/todo 4-0` == `/todo 4 -s` aimed at session -0 — with or without an explicit
    # `-s`. Gated on the ref actually resolving to a task, so a `<seq>` matching
    # nothing still falls through to the ordinary no-match + listing below. Only a
    # single bare `<seq>-<n>` token matches here; comma lists and the plain
    # `<seq> -s` multi-jump are left entirely to the `-s` branch.
    if _parse_ordinal_ref(arg) is not None:
        print(_jump_one(arg, a.session))
        return
    if jump:
        # -s: jump straight into the task's working session in a FRESH window
        # (leaving this one untouched). The ref before -s may be a comma list
        # (`/todo 1,2,5 -s`): attach + open one window and emit one
        # [SESSION-JUMP] block PER task. A single number is just a list of one.
        # Opening happens here so it's immediate and deterministic; each block
        # falls back to printing its one-liner if its window can't open.
        refs = [r.strip() for r in arg.split(",") if r.strip()]
        if not refs:
            print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
            return
        print("\n\n".join(_jump_one(r, a.session) for r in refs))
        return
    if arg.lower() in ("board", "board open", "board --open"):
        # /todo board → render the visual HTML board and open it (default).
        out = write_board()
        opened = _open_path(out)
        print("[BOARD] Your visual task board:\n  %s" % out)
        print("  Opened in your browser." if opened
              else "  Open it with:  open \"%s\"" % out)
        return
    task = resolve_ref(arg)
    if not task:
        print("No task matching '%s'.\n\n%s" % (arg, _fmt_list()))
        return
    touch(task, session=a.session, note="resumed", reopen=True)
    # Viewing the detail counts as "seen" — the render already surfaces recent
    # activity, so advance the delta high-water mark to avoid re-injecting the
    # same news on this session's next prompt/session-start.
    mark_seen(task, a.session)
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    print(_format_detail(task, a.session))


def _add_project_one(ref, project):
    """Record `project` on the task named by `ref` (idempotent). Returns an error
    line for a bad ref, or None on success (success stays silent — this is
    machine-called by delegate)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "add-project: no task matching %r" % ref
    projs = task.setdefault("projects", [])
    if project not in projs:
        projs.append(project)
        task["updated_ts"] = _now()
        save_task(task)
    return None


def cmd_add_project(a):
    """Record that a task has delegated work into a repo (project). Idempotent.

    Called by delegate.py when a worker is spawned with --seq, so /todo can
    list the task's in-project workers in its detail view. No session attach, no
    activity-log entry — keeps the link bookkeeping quiet. `--task` accepts a
    comma-separated list (record the project on several tasks at once); a bad ref
    is reported on stderr without aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("add-project: no task given\n")
        return
    for r in refs:
        err = _add_project_one(r, a.project)
        if err:
            sys.stderr.write(err + "\n")


def cmd_add_cost(a):
    """Accumulate a delegate run's worker cost (USD) onto a task, AND — when the
    optional per-run detail flags are present (--model / --session / --usage-json) —
    append a per-run record to task['runs']. Called by delegate.py after it parses the
    worker's JSON, so per-run cost lands on the linked /todo task's `cost` field (total
    + run count) plus a per-invocation breakdown — not just workers.json. No attach, no
    activity-log entry (quiet bookkeeping, like add-project); a bad ref / non-positive
    amount is a silent no-op on stderr."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-cost: no task matching %r\n" % a.task)
        return
    model = getattr(a, "model", None)
    session = getattr(a, "session", None)
    seq_label = getattr(a, "seq_label", None)
    category = getattr(a, "category", None) or "real"   # real | wasted (RESOLVED #4)
    usage_json = getattr(a, "usage_json", None)
    usage = None
    if usage_json:
        try:
            usage = json.loads(usage_json)
        except (TypeError, ValueError):
            usage = None                          # malformed usage → record the run without token detail
    has_detail = bool(model or session or usage or seq_label)
    try:
        amount = float(a.usd)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0 and not has_detail:
        return                                    # nothing to record (unchanged no-op)

    def _apply(t):                                # concurrent-safe: parallel workers'
        add_cost(t, a.usd, category=category)     # costs + run records both accumulate
        if has_detail:
            record_run(t, seq_label=seq_label, session_id=session, model=model,
                       cost_usd=a.usd, usage=usage, category=category)
        t["updated_ts"] = _now()
    mutate(task["id"], _apply)


def cmd_add_event(a):
    """Append one entry to a task's bounded event feed — quiet bookkeeping (loads,
    add_event, save; no touch, no attach, no activity-log entry, like add-cost). Used
    by the delegate to post worker/child milestones onto the linked /todo task so a
    resumed session's delta-brief can surface them. A bad ref is a silent no-op on
    stderr; exits 0 either way so a best-effort caller never fails on it."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-event: no task matching %r\n" % a.task)
        return
    kind, text, session = a.kind, getattr(a, "text", "") or "", getattr(a, "session", None)

    def _apply(t):                                # concurrent-safe: two workers'
        add_event(t, kind, text, session)         # events both survive a race
        t["updated_ts"] = _now()
    mutate(task["id"], _apply)
    _stream_emit("task.event", task,
                 {"kind": kind, "text": (text or "")[:EVENT_TEXT_MAX]}, session)


def cmd_add_ledger(a):
    """Quiet bookkeeping (same contract as add-event: no touch, no attach, exit 0
    on a bad ref) — delegate.py posts spawn/resume/stop/adopt/finish/crash here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("add-ledger: no task matching %r\n" % a.task)
        return
    def _apply(t):
        add_ledger(t, a.action, worker_sid=getattr(a, "worker", None),
                   actor_sid=getattr(a, "session", None),
                   detail=getattr(a, "detail", None))
        t["updated_ts"] = _now()
    mutate(task["id"], _apply)


def cmd_register_worker(a):
    """Quiet bookkeeping (same contract as add-ledger: no touch, no attach, exit 0
    on a bad ref) — delegate.py rosters a worker session on the task record
    (name/model/harness/status) on spawn + each terminal transition (#463)."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        sys.stderr.write("register-worker: no task matching %r\n" % a.task)
        return
    def _apply(t):
        register_worker_session(t, a.session, name=getattr(a, "name", None),
                                model=getattr(a, "model", None),
                                harness=getattr(a, "harness", None) or "claude",
                                status=getattr(a, "status", None) or "running")
        t["updated_ts"] = _now()
    mutate(task["id"], _apply)


def _memo_target(a):
    """Resolve the task for a `memo ack`/`show`: an explicit --task (any seq/id-prefix)
    else the acting session's attached task. Returns the task dict or None."""
    ref = getattr(a, "task", None)
    if ref:
        return resolve_ref(ref) or load_task(ref)
    tid = get_link(getattr(a, "session", None))
    if tid and tid != SKIP_SENTINEL:
        return load_task(tid)
    return None


def cmd_memo(a):
    """`task-station memo send|ack|show` — hand a fact/decision to a task's working
    session(s). `send --task <ref>` posts onto any task (attached or not, cross-task via
    resolve_ref); `ack`/`show` default --task to the acting session's attached task.
    A bad ref / unknown id prints ONE error line and returns (exit 0, like add-event),
    so a best-effort caller never aborts."""
    sub = getattr(a, "sub", None)
    if sub == "send":
        ref = getattr(a, "task", None)
        task = (resolve_ref(ref) or load_task(ref)) if ref else None
        if not task:
            print("memo: no task matching %r" % ref)
            return
        text = getattr(a, "text", "") or ""
        if not text.strip():
            print("memo: --text is required (the memo body)")
            return
        corrects = [c for c in (getattr(a, "corrects", None) or []) if str(c).strip()]
        memo = memo_send(task, text, from_sid=getattr(a, "session", None),
                         corrects=corrects)
        task["updated_ts"] = _now()
        save_task(task)
        print("memo %s → task #%s (%s)"
              % (memo["id"][:8], task.get("seq", task["id"][:8]), task["title"]))
        if corrects:
            print("  corrects: %s — the ack will require a disposition that engages it."
                  % ", ".join(corrects))
        else:
            # M4 backstop: `--corrects` only helps when the sender remembers it. Warn on
            # correction-shaped language; never block — the sender may have good reason.
            hits = correction_language(text)
            if hits:
                print("  ⚠ this reads like a correction (matched: %s) but declares no "
                      "--corrects target." % ", ".join(hits))
                print("    Add `--corrects <memory-slug|decision:N|memo-id8>` so the ack "
                      "has to engage what it replaces.")
        return

    task = _memo_target(a)
    if not task:
        print("memo: no task — pass --task <ref>, or attach this session first.")
        return

    if sub == "ack":
        memo, err = _memo_by_prefix(task, getattr(a, "id", None))
        if err:
            print(err)
            return
        sid = getattr(a, "session", None)
        if not sid:
            print("memo ack: --session <your-session-id> is required.")
            return
        dec = getattr(a, "decision", None)
        # M1: an ack must carry EXACTLY ONE disposition. A bare ack is refused — it was
        # the shape that let a correction be acknowledged and never integrated.
        disp, err = memo_ack_disposition(decision=dec,
                                         memory=getattr(a, "memory", None),
                                         noop=getattr(a, "noop", None))
        if err:
            print(err)
            corrects = memo_corrections(memo)
            if corrects:
                print("memo %s CORRECTS %s — it cannot be acked without saying what you "
                      "did about that." % (memo["id"][:8], ", ".join(corrects)))
            return
        decisions_before = len(task.get("decisions") or [])
        result = memo_ack(task, memo, sid,
                          promote=(disp["kind"] == "decision"),
                          decision_text=(disp["value"] if disp["kind"] == "decision" else None),
                          disposition=disp)
        task["updated_ts"] = _now()
        save_task(task)
        if result == "already":
            print("memo %s already acked by %s." % (memo["id"][:8], sid[:8]))
            return
        tail = {"decision": " → promoted to a decision",
                "memory": " → folded into memory note '%s'" % disp["value"],
                "noop": " → no durable change (%s)" % disp["value"]}[disp["kind"]]
        print("memo %s acked by %s%s." % (memo["id"][:8], sid[:8], tail))
        # A promoted memo body is UNCAPPED, so this is the other way a very long
        # decision gets written. Same advisory, same rule: it is already stored in
        # full, and this only suggests splitting it. Gated on the log actually having
        # GROWN, so a promote that no-oped on blank text can't warn about the entry
        # that was already there.
        n = len(task.get("decisions") or [])
        if n > decisions_before:
            warn = _dec.length_warning(task["decisions"][n - 1], n)
            if warn:
                print("  ⚠ %s" % warn)
        # M4: the memo read like a correction but declared no target — remind the acker
        # to go update the durable stores, which is the step that actually gets missed.
        if not memo_corrections(memo) and correction_language(memo.get("text")):
            print("  ⚠ REMINDER: this memo reads like a correction (matched: %s)."
                  % ", ".join(correction_language(memo.get("text"))))
            print("    An ack is a receipt, not an integration — update the durable store "
                  "it contradicts (agent memory / a task decision) now.")
        return

    if sub == "show":
        mid = getattr(a, "id", None)
        if not (task.get("memos") or []):
            print("(no memos)")
            return
        if mid:
            memo, err = _memo_by_prefix(task, mid)
            if err:
                print(err)
                return
            print(_format_memo_full(task, memo))
        else:
            print(_format_memo_list(task, getattr(a, "session", None)))
        return

    print("memo: use `send`, `ack`, or `show`.")


def _obsidian_vault_or_msg():
    """The configured, existing vault path — or None after printing WHY (off /
    missing). Shared by --sync-all and --flush so both fail the same way."""
    vault = _obsidian_vault()
    if not vault:
        print("Obsidian export is off. Enable it first:\n"
              '  /task-station:config --obsidian-vault "~/Documents/Obsidian Vault"')
        return None
    if not os.path.isdir(vault):
        print("Obsidian vault path does not exist: %s" % vault)
        return None
    return vault


def _flush_obsidian_dirty(vault, dirty, quiet=False):
    """Re-export each pending-resync task into `vault`. On success clear BOTH the
    dirty and persistent-failure flags; on failure keep dirty and SET
    obsidian_flush_failed — the persistent signal a later mutation reads to decide
    whether to surface the loud hint (this is what makes the mid-turn failure stay
    silent until a hook flush ALSO fails). Returns (flushed, failed). Best-effort per
    task — one task's failure never aborts the rest, nor breaks the calling hook."""
    import obsidian_sync
    flushed = failed = 0
    _begin_subgroups()   # one full-board detection for the whole flush batch
    try:
        for t in dirty:
            try:
                usage, prompts = _obsidian_note_data(t)
                related = _obsidian_related_links(t)
                if obsidian_sync.export_task(t, vault, usage=usage, prompts=prompts,
                                             related=related, owner=_owner()):
                    changed = clear_obsidian_dirty(t)
                    if t.pop("obsidian_flush_failed", None) is not None:
                        changed = True
                    if changed:
                        save_task(t)
                    flushed += 1
                else:
                    if not t.get("obsidian_flush_failed"):
                        t["obsidian_flush_failed"] = True
                        save_task(t)
                    failed += 1
            except Exception as e:
                if not t.get("obsidian_flush_failed"):
                    t["obsidian_flush_failed"] = True
                    save_task(t)
                failed += 1
                if not quiet:
                    sys.stderr.write("task-station: flush of task %s failed: %s\n"
                                     % (t.get("seq", t.get("id", "?")), e))
    finally:
        _end_subgroups()
    return flushed, failed


def cmd_obsidian(a):
    """`task-station obsidian` — manage the opt-in Obsidian export.

      --status    (default) report whether export is on, the vault + folder, note
                  count, the daily-note setting, and how many tasks are pending
                  resync (failed in-session exports awaiting a --flush).
      --sync-all  full resync: (re)write a note for EVERY task into the vault, and
                  clear the pending-resync flag on each that exports cleanly. Use
                  after enabling export, or to repair the vault after edits.
      --flush     re-export ONLY the pending-resync (obsidian_dirty) tasks and clear
                  their flags — the cheap way to drain the sandbox backlog. Run from
                  an unsandboxed shell; ALSO invoked automatically (with --quiet) by
                  the Stop / SessionStart hooks, which run unsandboxed and so heal a
                  protected-folder vault the sandboxed hot path couldn't write.
      --quiet     (with --flush) suppress all happy-path output — for the hooks, which
                  must stay silent; a persistent failure still arms the next mutation's
                  hint. A cheap no-op when export is off or nothing is pending.
    """
    if getattr(a, "flush", False):
        quiet = getattr(a, "quiet", False)
        vault = _obsidian_vault()
        # Cheap pre-checks first so the hooks spawn NO work on the happy path.
        if not vault:
            if not quiet:
                print("Obsidian export is off. Enable it first:\n"
                      '  /task-station:config --obsidian-vault "~/Documents/Obsidian Vault"')
            return
        # Cheap pre-check BEFORE any work: on the happy path (the common hook case)
        # there's nothing dirty, so we return without ensure_seqs / re-scan.
        if not any(obsidian_dirty(t) for t in all_tasks()):
            if not quiet:
                print("Nothing to flush — no tasks pending resync.")
            return
        ensure_seqs()   # every note filename needs a stable seq; build the list AFTER
        dirty = [t for t in all_tasks() if obsidian_dirty(t)]
        import obsidian_sync
        if not os.path.isdir(vault):
            # Vault configured but gone (unmounted / typo) — a genuine PERSISTENT
            # failure: arm the hint on the dirty tasks (the next mutation surfaces it)
            # and stay silent for hook callers; explain for a manual invocation.
            for t in dirty:
                if not t.get("obsidian_flush_failed"):
                    t["obsidian_flush_failed"] = True
                    save_task(t)
            if not quiet:
                print("Obsidian vault path does not exist: %s" % vault)
                sys.stdout.write(_obsidian_persistent_help(vault))
            return
        flushed, failed = _flush_obsidian_dirty(vault, dirty, quiet)
        if flushed:
            _clear_obsidian_perm_marker()   # backlog drained — re-arm the one-shot hint
            _sync_obsidian_category_hubs(vault, _owner())   # refresh hubs for the drained tasks
            _sync_obsidian_story_hubs(vault, _owner())
        if not quiet:
            print("Flushed %d task%s → %s%s"
                  % (flushed, "" if flushed == 1 else "s", obsidian_sync.plugin_dir(vault),
                     "" if not failed else "; %d still failing" % failed))
            if failed:
                sys.stdout.write(_obsidian_persistent_help(vault))
        return
    if getattr(a, "sync_all", False):
        vault = _obsidian_vault_or_msg()
        if not vault:
            return
        import obsidian_sync
        ensure_seqs()                       # every note filename needs a stable seq
        owner = _owner()
        # When an owner is (newly) set, relocate any existing FLAT notes into the owner
        # subtree first, so the resync updates them in place and the old location is
        # left with no managed orphans.
        migrated = obsidian_sync.migrate_to_owner(vault, owner) if owner else 0
        n = 0
        _begin_subgroups()   # one full-board detection for the whole resync loop
        try:
            for t in all_tasks():
                try:
                    usage, prompts = _obsidian_note_data(t)
                    related = _obsidian_related_links(t)
                    if obsidian_sync.export_task(t, vault, usage=usage, prompts=prompts,
                                                 related=related, owner=owner):
                        changed = clear_obsidian_dirty(t)
                        if t.pop("obsidian_flush_failed", None) is not None:
                            changed = True    # a clean resync fully heals any pending flags
                        if changed:
                            save_task(t)
                        n += 1
                except Exception as e:
                    sys.stderr.write("task-station: export of task %s failed: %s\n"
                                     % (t.get("seq", t.get("id", "?")), e))
        finally:
            _end_subgroups()
        if n:
            _clear_obsidian_perm_marker()
        # Regenerate the category-hub pages once, from the now-current sidecar (toggle
        # off prunes them) — a full resync repairs the hubs like it repairs index.md.
        _sync_obsidian_category_hubs(vault, owner)
        _sync_obsidian_story_hubs(vault, owner)
        dest = obsidian_sync.owner_dir(obsidian_sync.plugin_dir(vault), owner)
        print("Synced %d task%s → %s%s"
              % (n, "" if n == 1 else "s", dest,
                 "" if not migrated else " (relocated %d existing note%s into %s/)"
                 % (migrated, "" if migrated == 1 else "s", owner)))
        return
    # default / --status
    import obsidian_sync
    print(obsidian_sync.status_report())
    # Pending-resync count — only meaningful while export is on; a stale flag from a
    # since-disabled vault shouldn't nag with a --flush that can't run.
    if _obsidian_vault():
        pending = sum(1 for t in all_tasks() if obsidian_dirty(t))
        if pending:
            print("  Pending resync: %d task%s — run `obsidian --flush` from an "
                  "unsandboxed shell." % (pending, "" if pending == 1 else "s"))


def _live_task_ids():
    """The set of task ids whose exported notes should SURVIVE a prune/delete
    reconcile: every task that still exists AND has not been redacted (a redacted
    task's content is being forgotten, so its notes go too)."""
    return {t.get("id") for t in all_tasks() if t.get("id") and not t.get("redacted")}


def _export_dirs():
    """The recorded generic-export destinations (config `export_dirs`) — the sidecar
    indexes `delete` can locate to purge a hard-deleted task's notes. Expanded,
    de-duplicated, existing dirs only; a bad/missing entry is skipped."""
    try:
        import config as _cfg
        raw = _cfg.get("export_dirs") or []
    except Exception:
        raw = []
    out, seen = [], set()
    for p in raw:
        d = os.path.expanduser(p or "")
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _record_export_dir(out_dir):
    """Remember `out_dir` as a generic-export destination so a later `delete`/`--prune`
    can find its sidecar index. Stored un-expanded (survives a home move), deduped,
    capped. Best-effort — a config hiccup never breaks the export."""
    try:
        import config as _cfg
        cur = _cfg.get("export_dirs") or []
        if not isinstance(cur, list):
            cur = []
        if out_dir in cur:
            return
        cur = ([out_dir] + [d for d in cur if d != out_dir])[:32]
        _cfg.set("export_dirs", cur)
    except Exception:
        pass


def cmd_export(a):
    """`task-station export --dir <path>` — the generic, vault-independent episodic
    export. Writes the same per-task Markdown notes the Obsidian bridge produces
    (shared render_note) plus a wikilinked `index.md` into ANY directory, with no
    vault config required — a self-sufficient snapshot a second brain can ingest.

      --dir <path>       destination directory (created if absent). REQUIRED.
      --task <ref>       export ONE task (seq/id); or
      --status <s>       export only open|active|closed tasks; or
      --all              export every task (the default when no selector is given).
      --include a,b,c    sections to render: usage,prompts,history (default
                         usage,history — prompts is opt-in, a snapshot may leave
                         the machine).
      --since <ISO date> only tasks updated at/after this date.
      --prune            reconcile --dir against live tasks: drop notes whose task no
                         longer exists (or was redacted) + rebuild index.md.

    Sandbox note: a sandboxed session can only write under its cwd + $TMPDIR, so an
    export into an arbitrary directory must run from the hub (cwd ~) or an
    unsandboxed context — a denied write surfaces with a hint (the same constraint
    as --obsidian-vault into a protected root)."""
    out_dir = getattr(a, "dir", None)
    if not out_dir:
        print("usage: task-station export --dir <path> "
              "[--task <ref> | --all | --status open|active|closed] "
              "[--include usage,prompts,history] [--since <ISO date>] [--prune]")
        return
    out_dir = os.path.expanduser(out_dir)
    import export as _export, obsidian_sync
    ensure_seqs()   # every note filename needs a stable seq
    owner = _owner()
    if getattr(a, "prune", False):
        # Prune reconciles ONLY this owner's subtree — another owner's notes in a
        # shared dir are never touched.
        scoped = obsidian_sync.owner_dir(out_dir, owner)
        if not os.path.isdir(scoped):
            print("Nothing to prune — no export dir at %s." % scoped)
            return
        removed = _export.prune_dir(scoped, _live_task_ids())
        # Reconcile the category hubs too: a category whose last note was pruned loses
        # its hub page (and toggle-off prunes them all). Idempotent when nothing moved.
        _export.sync_category_hubs(scoped, enabled=_category_hubs_on(),
                                   subgroups=_subgroups_on())
        # Story hubs are the orthogonal axis — reconcile them the same way.
        _export.sync_story_hubs(scoped, enabled=_story_groups_on())
        if removed:
            _record_export_dir(out_dir)
            print("Pruned %d orphan note%s from %s (index.md updated)."
                  % (len(removed), "" if len(removed) == 1 else "s", scoped))
        else:
            print("Nothing to prune — %s is already in sync with live tasks." % scoped)
        return
    ref = getattr(a, "task", None)
    if ref:
        t = resolve_ref(ref) or load_task(ref)
        if not t:
            print("No task matching '%s'." % ref)
            return
        tasks = [t]
    else:
        status = getattr(a, "status", None)
        tasks = all_tasks()
        if status:
            want = STATUS_INPUT_ALIASES.get(status, status)
            tasks = [t for t in tasks if task_status(t) == want]
    include = _export.parse_include(getattr(a, "include", None))
    _usage_engine()   # point usage's path globals before note_context reads the ledger
    _begin_subgroups()   # one full-board detection for the whole export
    try:
        try:
            entries = _export.export_tasks(out_dir, tasks, _backend(),
                                           include=include, since=getattr(a, "since", None),
                                           related_fn=_export_related_fn, owner=owner,
                                           category_hubs=_category_hubs_on(),
                                           subgroups=_subgroups_on(),
                                           story_groups=_story_groups_on())
        except OSError as e:
            print("Could not write to %s: %s" % (out_dir, e))
            print("  A sandboxed session can only write under its cwd + $TMPDIR — run "
                  "`export` from the hub (cwd ~) or an unsandboxed shell, or pick a "
                  "directory inside the allowed roots.")
            return
        _record_export_dir(out_dir)   # so a later delete/--prune can locate this sidecar
        print("Exported %d task%s → %s (index.md + %s)"
              % (len(entries), "" if len(entries) == 1 else "s",
                 obsidian_sync.owner_dir(out_dir, owner),
                 ", ".join(sorted(include)) or "notes"))
    finally:
        _end_subgroups()


def _render_usage(task, data):
    """Terminal render of `usage --task`: the per-model mix, a per-session table
    (short sid, role+label, tokens, derived $), totals with the reported $ cross-
    check, and the derivation footnote."""
    seq = task.get("seq")
    out = ["Usage — Task [%s]%s  %s"
           % (task["id"][:8], (" #%s" % seq) if seq else "", task["title"])]
    models = data.get("models") or {}
    if not models:
        out.append("  No usage tracked yet — run `usage --task %s --refresh`, "
                   "or let the Stop/SessionStart hooks flush it."
                   % (seq if seq else task["id"][:8]))
        return "\n".join(out)
    import pricing
    ranked = sorted(models.items(), key=lambda kv: -kv[1].get("pct", 0))
    mix = []
    for m, d in ranked:
        fam = pricing.model_family(m)
        pct = round(d.get("pct", 0) * 100)
        mix.append("%s %d%%%s" % (fam, pct, " (unpriced)" if d.get("cost_usd") is None else ""))
    out.append("  Models:  " + " · ".join(mix))
    phases = data.get("phases") or {}
    ranked_p = sorted(phases.items(), key=lambda kv: -kv[1].get("pct", 0))
    pmix = ["%s %d%%" % (name.capitalize(), round(d.get("pct", 0) * 100))
            for name, d in ranked_p if round(d.get("pct", 0) * 100) > 0]
    if pmix:
        out.append("  Phases:  " + " · ".join(pmix))
    if data.get("sessions"):
        out.append("  Sessions (hub / worker):")
        for s in data["sessions"]:
            label = ("/%s" % s["label"]) if s.get("label") else ""
            # An unpriced (unknown-model) session shows an em-dash, never `$n/a`.
            cost = "—" if s.get("cost_usd") is None else "$%.4f" % s["cost_usd"]
            out.append("    %-8s %-7s%-14s in %d · out %d · cache-read %d · %s"
                       % (s["sid"], s["role"], label, s["in"], s["out"],
                          s["cache_read"], cost))
    # Never-`n/a` total: the priced subtotal reads as a `≥` floor when a model is
    # unknown; the delegate-reported $ stays as the cross-check when present.
    sc = _stats_cost(data)
    rep = data.get("reported_cost_usd") or 0.0
    if sc["kind"] == "reported":
        line = "  Totals:  in %d · out %d · %s" \
            % (data.get("total_in", 0), data.get("total_out", 0), sc["text"])
    else:
        line = "  Totals:  in %d · out %d · %s derived" \
            % (data.get("total_in", 0), data.get("total_out", 0), sc["text"])
        if rep > 0:
            line += " · $%.2f reported" % rep
    out.append(line)
    out.append("  Derivation: %s" % data.get("derived_note", ""))
    return "\n".join(out)


def cmd_usage(a):
    """`task-station usage` — the WS1 usage ledger surface.

      --task <ref>   render per-task usage: model mix, per-session breakdown
                     (hub vs each worker), token/$ totals, derivation footnote.
      --refresh      (re)scan the task's transcripts before rendering.
      --json         emit the raw task_usage() dict instead of the text render.
      --flush [--quiet]  hook entry point: incrementally rescan every open/active
                     task's sessions. Never crashes the hook path (errors → stderr,
                     stale numbers); a cheap no-op when usage tracking is off.
    """
    import config as _cfg
    usage = _usage_engine()
    store = _backend()
    mode = getattr(a, "mode", None)
    if mode == "scan-all":
        if not _cfg.usage_tracking_enabled():
            print("Usage tracking is off (config --usage off) — nothing scanned.")
            return
        n = usage.scan_all(store)
        print("Scanned %d session transcript%s into the usage ledger."
              % (n, "" if n == 1 else "s"))
        return
    if mode == "import-costbar":
        res = usage.import_costbar(store, path=getattr(a, "path", None))
        if res.get("error"):
            print("import-costbar: %s" % res["error"])
            return
        print("Imported %d costbar session%s (%d already in the ledger, %d still "
              "have a live transcript) from %s."
              % (res["imported"], "" if res["imported"] == 1 else "s",
                 res["skipped_in_ledger"], res["skipped_has_transcript"], res["path"]))
        return
    if getattr(a, "flush", False):
        quiet = getattr(a, "quiet", False)
        if not _cfg.usage_tracking_enabled():
            return
        n = 0
        for t in all_tasks():
            if task_status(t) not in STATUS_BOARD:
                continue
            try:
                usage.refresh_task(store, t)   # attributes hub + worker sessions first
                n += 1
            except Exception as e:
                sys.stderr.write("usage --flush: task %s failed: %s\n"
                                 % (t.get("seq", t.get("id", "?")), e))
        # Then sweep every remaining transcript so Week/Total reflect all sessions
        # (unattached ones land task_id NULL). Incremental → cheap; must run AFTER
        # refresh_task so a shared/worker session keeps its task attribution.
        try:
            usage.scan_all(store)
        except Exception as e:
            sys.stderr.write("usage --flush: scan-all failed: %s\n" % e)
        if not quiet:
            print("Usage flushed for %d task%s." % (n, "" if n == 1 else "s"))
        return
    if not getattr(a, "task", None):
        print("usage: task-station usage --task <ref> [--refresh] [--json]")
        return
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    if getattr(a, "refresh", False):
        try:
            usage.refresh_task(store, task)
        except Exception as e:
            sys.stderr.write("usage --refresh failed: %s\n" % e)
    data = usage.task_usage(store, task)
    if getattr(a, "as_json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print(_render_usage(task, data))


def _prompt_ts(ts):
    """Epoch-seconds → a compact local 'YYYY-MM-DD HH:MM' for the prompt trail
    (prompts store ts as epoch seconds). '?' when absent/unparseable so a row with
    no timestamp still renders."""
    if ts is None:
        return "?"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def _prompt_session_tag(p):
    """The per-prompt session attribution: `hub <sid>` / `worker:<label> <sid>` /
    `<role> <sid>`, mirroring the board's session-role label. '<sid>' is the short
    (8-char) session id; a missing sid collapses to '—'."""
    role = p.get("role") or "unknown"
    if p.get("label"):
        role = "%s:%s" % (role, p["label"])
    return "%s %s" % (role, p.get("sid") or "—")


def _assistant_text(msg):
    """The concatenated text of an assistant transcript message — the `text` blocks of
    a list `content`, or a plain-string content. Tool-use blocks (no text) are skipped.
    '' when there's no text (a pure tool-use turn)."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


_BULLET_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+")


def _last_bullet_reply(text):
    """Claude's reply excerpt for the prompt view: the ENTIRE response tail from the
    LAST bullet line (`-`/`*`/`•`/`1.`) to the end — not just that single line. When the
    response has no bullets, the last non-empty paragraph (everything after the last
    blank line). The leading bullet marker and inline bold/code emphasis are stripped,
    intra-line whitespace collapsed; line breaks are KEPT (the board renders them; the
    terminal/markdown views flatten). '' when the response is empty."""
    raw = [ln.rstrip() for ln in (text or "").splitlines()]
    while raw and not raw[-1].strip():
        raw.pop()
    if not any(ln.strip() for ln in raw):
        return ""
    start = None
    for i, ln in enumerate(raw):
        if _BULLET_RE.match(ln):
            start = i
    if start is None:                       # no bullets → the last paragraph
        start = 0
        for i, ln in enumerate(raw):
            if not ln.strip():
                start = i + 1
    out = []
    for ln in raw[start:]:
        if not ln.strip():
            continue
        ln = _BULLET_RE.sub("", ln)         # only raw[start] can carry a marker
        ln = re.sub(r"\*\*|__|`", "", ln)   # drop md bold/code emphasis
        out.append(" ".join(ln.split()))
    return "\n".join(out)


def _prompt_replies_all(path):
    """{prompt_uuid: last-Claude-bullet} for EVERY human prompt in the transcript at
    `path` — the whole map, from one pass. `_prompt_replies` filters it down.

    Cached whole rather than per requested uuid-set, because a board render asks the
    same transcript for different subsets of its prompts (once per task that session
    touched) and the PARSE, not the filtering, is the expense. Whole-map is also
    exactly equivalent: a reply is bounded by its own turn — `last_text` resets at
    every real user line — so a prompt's reply never depends on which prompts were
    asked for.

    Best-effort: a read that fails partway returns what it had, as the per-call
    version did. An unchanged file fails the same way on a retry, so that partial
    result is cacheable."""
    replies = {}
    cur = None        # uuid of the human prompt whose reply we're collecting (or None)
    last_text = ""    # the last assistant text block seen in the current turn
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("type")
                if t == "user":
                    if o.get("isSidechain"):
                        continue
                    content = (o.get("message") or {}).get("content")
                    # a tool_result user line is a continuation, not a new human turn
                    if isinstance(content, list) and any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in content):
                        continue
                    if cur is not None and last_text:
                        replies[cur] = _last_bullet_reply(last_text)
                    u = o.get("uuid")
                    cur = u if u else None      # a line with no uuid can own no reply
                    last_text = ""
                elif t == "assistant" and cur is not None:
                    # `cur` is now set for EVERY prompt, not just requested ones, so
                    # this runs on assistant lines the per-call version skipped. A
                    # non-dict `message` (malformed transcript) would have raised
                    # straight out of a Stop hook; treat it as no text instead.
                    msg = o.get("message")
                    txt = _assistant_text(msg) if isinstance(msg, dict) else ""
                    if txt.strip():
                        last_text = txt
        if cur is not None and last_text:
            replies[cur] = _last_bullet_reply(last_text)
    except OSError:
        return replies
    return replies


def _prompt_replies(session_id, uuids):
    """{prompt_uuid: last-Claude-bullet} for the given user-prompt uuids, read from the
    session's transcript. For each prompt, the reply is the LAST assistant text block in
    the turn that FOLLOWS it (before the next real user turn) — tool-result/sidechain
    lines are skipped so a tool round-trip doesn't split the turn. Best-effort: '' /
    missing entries when the transcript is unavailable or the turn had no text.

    The transcript is parsed at most once per (file, version) per process — see the
    transcript-cache note above _session_msgcount. Only the requested uuids come back,
    so the returned dict is identical to what the uncached per-call parse produced."""
    want = {u for u in uuids if u}
    if not session_id or not want:
        return {}
    path = _find_session_path(session_id)
    if not path:
        return {}
    key = _stat_key(path)
    if key is None:                             # unstat-able → no key, parse as before
        return {u: r for u, r in _prompt_replies_all(path).items() if u in want}
    memkey = (path, key[0], key[1])
    full = _REPLIES_MEM.get(memkey)             # `{}` is a valid hit, hence `is None`
    if full is None:
        full = _mem_put(_REPLIES_MEM, memkey, _prompt_replies_all(path), REPLIES_CACHE_MAX)
    return {u: r for u, r in full.items() if u in want}


def _human_prompts_with_replies(prompts):
    """Filter `prompts` to genuine human-typed ones (drops slash-commands, compaction
    summaries, and hook/managed wrappers via _prompt_is_human) and attach each one's
    last-Claude-bullet `reply`, read from the transcripts (one read per session). Returns
    the filtered rows (each a copy with a `reply` key), oldest-first order preserved."""
    human = [p for p in prompts if _prompt_is_human(p.get("kind"), p.get("text"))]
    by_session = {}
    for p in human:
        by_session.setdefault(p.get("session_id"), []).append(p.get("uuid"))
    replies = {}
    for sid, uuids in by_session.items():
        replies.update(_prompt_replies(sid, uuids))
    out = []
    for p in human:
        q = dict(p)
        q["reply"] = replies.get(p.get("uuid"), "")
        out.append(q)
    return out


def _format_prompts(task, prompts, include_compact=False):
    """Terminal render of `prompts --task`: by DEFAULT the curated view — only genuine
    human-typed prompts, each followed by Claude's last-bullet reply (`↳ …`), oldest
    first, session-attributed. `--all` (include_compact=True) instead prints the RAW,
    complete trail (every kind: commands, compaction rows, wrappers) with no replies —
    the escape hatch. Long lines are clipped for the terminal (full text in --json/--md)."""
    seq = task.get("seq", task["id"][:8])
    out = ["# Prompts — task %s · %s" % (seq, task.get("title", ""))]
    if not prompts:
        out.append("  No prompts captured for this task yet — prompt capture is on "
                   "by default (see `config --usage-prompts`); the Stop/SessionStart "
                   "hooks flush the ledger.")
        return "\n".join(out)
    if include_compact:
        # RAW trail (every row, no replies) — the --all escape hatch.
        for p in prompts:
            text = " ".join((p.get("text") or "").split())
            if len(text) > 100:
                text = text[:99].rstrip() + "…"
            out.append("%-16s  [%s]  %s"
                       % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), text))
        return "\n".join(out)
    rows = _human_prompts_with_replies(prompts)
    if not rows:
        out.append("  No human-typed prompts captured yet (only commands / generated "
                   "rows). Use `--all` for the complete raw trail.")
        return "\n".join(out)
    for p in rows:
        text = " ".join((p.get("text") or "").split())
        if len(text) > 100:
            text = text[:99].rstrip() + "…"
        out.append("%-16s  [%s]  %s"
                   % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), text))
        reply = " ".join((p.get("reply") or "").split())
        if reply:
            if len(reply) > 100:
                reply = reply[:99].rstrip() + "…"
            out.append("%-16s  %s↳ %s" % ("", " " * (len(_prompt_session_tag(p)) + 4), reply))
    return "\n".join(out)


def _format_prompts_md(task, prompts, include_compact=False):
    """Markdown render of the prompt trail — the SHAREABLE artifact ("show others exactly
    what I prompted to get the end result"). By DEFAULT the curated view: only human-typed
    prompts (full text, as a blockquote), each followed by Claude's last-bullet reply
    (`↳ …`). `--all` (include_compact=True) prints the RAW complete trail (every kind, no
    replies) — commands as a code span, prose as a blockquote. Oldest first."""
    seq = task.get("seq", task["id"][:8])
    out = ["# Prompts — task %s · %s" % (seq, task.get("title", "")), ""]
    if not prompts:
        out.append("_No prompts captured for this task yet._")
        return "\n".join(out)
    if include_compact:
        nsess = len({p.get("session_id") for p in prompts})
        out.append("_%d prompt%s · %d session%s · oldest first · times local._"
                   % (len(prompts), "" if len(prompts) == 1 else "s",
                      nsess, "" if nsess == 1 else "s"))
        out.append("")
        for p in prompts:
            kind = p.get("kind") or "prompt"
            suffix = {"command": " · command",
                      "compact": " · compaction summary"}.get(kind, "")
            out.append("**%s** · `%s`%s"
                       % (_prompt_ts(p.get("ts")), _prompt_session_tag(p), suffix))
            out.append("")
            text = p.get("text") or ""
            if kind == "command":
                out.append("`%s`" % " ".join(text.split()))
            else:
                for line in (text.splitlines() or [""]):
                    out.append("> %s" % line)
            out.append("")
        return "\n".join(out).rstrip() + "\n"
    rows = _human_prompts_with_replies(prompts)
    if not rows:
        out.append("_No human-typed prompts captured yet — use `--all` for the raw trail._")
        return "\n".join(out)
    nsess = len({p.get("session_id") for p in rows})
    out.append("_%d prompt%s · %d session%s · human prompts + Claude's reply · oldest first._"
               % (len(rows), "" if len(rows) == 1 else "s",
                  nsess, "" if nsess == 1 else "s"))
    out.append("")
    for p in rows:
        out.append("**%s** · `%s`" % (_prompt_ts(p.get("ts")), _prompt_session_tag(p)))
        out.append("")
        for line in ((p.get("text") or "").splitlines() or [""]):
            out.append("> %s" % line)
        reply = " ".join((p.get("reply") or "").split())
        if reply:
            out.append("")
            out.append("↳ %s" % reply)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _format_prompts_view(task):
    """The read-only `/todo <n> prompts` terminal view — the default (ascii, no
    compaction rows) prompt trail. Mirrors `/todo <n> history`: it renders, never
    attaches/reopens/mutates."""
    prompts = _usage_engine().task_prompts(_backend(), task)
    return _format_prompts(task, prompts)


def cmd_prompts(a):
    """`task-station prompts --task <ref> [--json|--md] [--all]` — the session-attributed
    trail of what drove a task, oldest first (hub + every delegated worker). By DEFAULT
    the curated view: only genuine human-typed prompts, each followed by Claude's
    last-bullet reply (read from the transcript) — commands, compaction rows, and
    hook/managed wrappers are filtered out.

      --json   emit the raw prompt-row list (uuid/ts/session/role/kind/text)
      --md     the shareable Markdown artifact (human prompts + replies; --all for raw)
      --all    the complete RAW trail (every kind, no replies) — the escape hatch
    """
    if not getattr(a, "task", None):
        print("usage: task-station prompts --task <ref> [--json|--md] [--all]")
        return
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    include_compact = bool(getattr(a, "all", False))
    prompts = _usage_engine().task_prompts(_backend(), task,
                                           include_compact=include_compact)
    if getattr(a, "as_json", False):
        print(json.dumps(prompts, indent=2, ensure_ascii=False))
        return
    if getattr(a, "as_md", False):
        print(_format_prompts_md(task, prompts, include_compact))
        return
    print(_format_prompts(task, prompts, include_compact))


def _tildify(path):
    """Collapse the user's home to `~` for a compact cwd column ('—' when empty)."""
    if not path:
        return "—"
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _format_session_row(r):
    """One live-session table line:
    `● <pid> · task <seq> · <role> · <status> · <age> · <cwd~> · <resume>`.
    The leading ● marks it as a real running process; missing joins collapse to '—'."""
    seq = r.get("task_seq")
    task_col = ("task %s" % seq) if seq is not None else "task —"
    return "● %-8s · %s · %-6s · %-4s · %-8s · %s · %s" % (
        r.get("pid", "?"),
        task_col,
        r.get("role") or "—",
        r.get("status") or "—",
        rel_time(r.get("updated_ts")),
        _tildify(r.get("cwd")),
        r.get("resume_command") or "—",
    )


def cmd_sessions(a):
    """`task-station sessions [--task <ref>] [--json]` — every ACTUALLY-running
    Claude Code session (hub + delegated workers), each with its task, busy/idle
    state, and a one-command resume. Dead/crashed sessions never appear. `--task`
    filters to one task's live sessions; `--json` emits the raw row list."""
    import live_sessions
    rows = live_sessions.running()
    if getattr(a, "task", None):
        t = resolve_ref(a.task) or load_task(a.task)
        rows = [r for r in rows if t and r.get("task_seq") == t.get("seq")]
    if getattr(a, "as_json", False):
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("No live Claude sessions." if not getattr(a, "task", None)
              else "No live Claude sessions for that task.")
        return
    for r in rows:
        print(_format_session_row(r))


def cmd_status(a):
    """Show or set a task's per-task state between the board states (○ new /
    ● active). `status --task <ref>` with no value reports the current state;
    `status --task <ref> new|active` sets it (idempotent — `new` is the input
    alias for the stored `open`). Closing goes through /done, not here — a closed
    task is reported but not settable from here."""
    task = resolve_ref(a.task) or load_task(a.task)
    if not task:
        print("No task matching '%s'." % a.task)
        return
    value = getattr(a, "value", None)
    cur = task_status(task)
    if not value:
        glyph = STATUS_GLYPH.get(cur, "")
        print("Task [%s] %s — status: %s %s"
              % (task["id"][:8], task["title"], glyph, status_display(cur)))
        return
    value = normalize_status_input(value)   # `new` → stored `open`
    if value not in STATUS_SETTABLE:
        if value == STATUS_CLOSED:
            print("status: close a task with /done (or `done --task %s`), not `status`."
                  % task.get("seq", task["id"][:8]))
        else:
            print("status: unknown status '%s' — use 'new' or 'active'." % value)
        return
    if is_closed(task):
        print("Task [%s] %s is closed — reopen it via /todo %s first."
              % (task["id"][:8], task["title"], task.get("seq", task["id"][:8])))
        return
    if set_status(task, value, note="status set to %s (manual)" % value,
                  session=getattr(a, "session", None)):
        save_task(task)
        maybe_refresh_board()   # open⇄active flip must show on the board NOW
        print("Task [%s] %s → %s %s"
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))
    else:
        print("Task [%s] %s already %s %s."
              % (task["id"][:8], task["title"], STATUS_GLYPH[value], status_display(value)))


def cmd_session_title(a):
    """Print the window/title-bar label for an attached session (or nothing).

    The SessionStart hook puts this in hookSpecificOutput.sessionTitle so the
    terminal reads `#<seq>: <title>` — the closest we get to auto-labelling
    the hub (the resume-NAME can't be set programmatically on a running session)."""
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    print("#%s: %s" % (task.get("seq", "?"), task["title"]))


def cmd_whoami(a):
    """Map any session id → its task. The backstop that identifies a session
    regardless of whether it was ever named."""
    task_id = get_link(a.session)
    porcelain = getattr(a, "porcelain", False)
    if task_id == SKIP_SENTINEL:
        if not porcelain:
            print("session %s: intentionally untracked (skipped)" % a.session[:8])
        return
    statusline = getattr(a, "statusline", False)
    if statusline:
        # HUD-INDEPENDENT capture: this provider runs whenever the statusline is on.
        # Persist the harness context-window size from the piped payload so the Stop
        # nudge sizes % against the REAL window even when the cost HUD is off. When the
        # HUD is on, its own provider (hud.observe) already captured it — skip here to
        # avoid a redundant read-modify-write race on the shared snapshot.
        try:
            import config as _cfg_cap
            if not _cfg_cap.hud_enabled():
                _cw = _read_statusline_stdin().get("context_window") or {}
                persist_harness_context_window(
                    a.session,
                    _cw.get("context_window_size") if isinstance(_cw, dict) else None)
        except Exception:
            pass
    task = load_task(task_id) if task_id else None
    if not task:
        if not porcelain and not statusline:
            print("session %s: not attached to any task" % a.session[:8])
        return
    ensure_seqs()
    if porcelain:
        # Machine-readable: '<seq>\t<seq>-<n>\t<kind>' (tab-separated). Field 1
        # stays the bare seq so pre-463 `cut -f1`-style consumers keep working;
        # delegate._attached_seq splits on whitespace and takes field 1.
        if ensure_ordinals(task):
            save_task(task)
        m = (task.get("session_meta") or {}).get(a.session) or {}
        kind = m.get("role") or "unknown"
        print("%s\t%s\t%s" % (task.get("seq", ""),
                              ordinal_label(task, a.session) or "", kind))
        return
    if statusline:
        # When the cost HUD is on it owns the whole bar and renders the task inline
        # in its own header (model badge + segment). Emit NOTHING here so the task
        # line never renders twice; the host skips an empty provider. Toggling the
        # HUD off restores this segment on the next render.
        try:
            import config as _cfg
            if _cfg.hud_enabled():
                return
        except Exception:
            pass
        # A ready-to-display, ANSI-colored segment for a status bar —
        # '#<seq>  <dot> [TAG]  <title>'. Self-contained: knows nothing about who
        # renders it. Honors --width (>0) by truncating the title so the whole
        # segment fits that many columns; --width 0 means no limit.
        print(statusline_segment(task, getattr(a, "width", 0),
                                  ordinal=hub_ordinal(task, a.session)))
        return
    print("session %s → task-station %s · %s (%s)"
          % (a.session[:8], task.get("seq", "?"), task["title"],
             status_display(task_status(task))))


def _update_one(ref, a):
    """Apply the update flags to the single task named by `ref` and return its
    result line(s). Never raises — a bad ref returns a no-match line — so a caller
    updating a comma list keeps going past it. The SAME flags are applied to every
    task in a batch (e.g. set several tasks' colour at once).

    The self-update runs through store.mutate (optimistic-locked) so two concurrent
    `/todo save`s can't clobber each other — appended decisions/steps/history all
    survive a reload-and-retry. --relate's cross-task `child` posts are side effects,
    so they run AFTER the atomic self-update, each itself optimistic-locked."""
    base = resolve_ref(ref) or load_task(ref)
    if not base:
        return "update: no task matching %r" % ref
    tid = base["id"]
    # The acting session (if any) attributes every event this update authors.
    session = getattr(a, "session", None)

    # Resolve --relate targets up front (reads only); the child-event posts they
    # trigger are deferred to after the self-update.
    relate_targets, relate_warnings = [], []
    for r2 in (getattr(a, "relate", None) or []):
        other = resolve_ref(r2) or load_task(r2)
        if not other:
            relate_warnings.append("update %s: ignoring --relate %s (no such task)" % (ref, r2))
        elif other.get("id") == tid:
            relate_warnings.append("update %s: ignoring --relate %s (a task can't relate to itself)" % (ref, r2))
        else:
            relate_targets.append(other)

    # The TYPED edges, resolved the same way and for the same reason: reads happen
    # here, the writes land inside the optimistic-locked self-update below, and the
    # ONE cross-task write (--replaces closing its target) is a deferred side effect.
    # A single value arrives as a string, a repeatable one as a list — normalise both,
    # and stringify: `resolve_ref` strips its argument, so a caller handing us a bare
    # int seq (a test, a future non-argparse entry point) must not blow up.
    def _reflist(v):
        vals = [] if v is None else (list(v) if isinstance(v, list) else [v])
        return [s for s in (str(x).strip() for x in vals if x is not None) if s]

    edge_targets, unrelate_targets = {}, []
    for flag, kind, vals in (
            ("--depends-on", "depends-on", _reflist(getattr(a, "depends_on", None))),
            ("--parent", "parent", _reflist(getattr(a, "parent", None))),
            ("--absorbed-by", "absorbed-by", _reflist(getattr(a, "absorbed_by", None))),
            ("--replaces", "replaces", _reflist(getattr(a, "replaces", None))),
            ("--duplicates", "duplicates", _reflist(getattr(a, "duplicates", None)))):
        for r2 in vals:
            other, why = _resolve_edge_ref(r2, tid, flag, kind)
            if other is None:
                relate_warnings.append("update %s: %s" % (ref, why))
            else:
                edge_targets.setdefault(kind, []).append(other)
    for r2 in _reflist(getattr(a, "unrelate", None)):
        other = resolve_ref(r2) or load_task(r2)
        if not other:
            relate_warnings.append("update %s: ignoring --unrelate %s (no such task)" % (ref, r2))
        else:
            unrelate_targets.append(other)

    # CYCLE CHECK — a WARNING, never a refusal. Direct precedent: the 600-char decision
    # advisory always stores, because a refusal makes the author drop a fact or fake two
    # entries out of one. The same holds here: refusing the write does not remove the
    # dependency, it only stops it being written down. See `_relation_cycle_path` for why
    # this is structurally incomplete and why every parent-chain walker must be cycle-safe.
    # The scan is lazy, so a plain --title update still reads nothing extra.
    cycle_warnings, _scan, absorb_children = [], None, set()
    for kind in ("depends-on", "parent"):
        for other in edge_targets.get(kind, []):
            if _scan is None:
                _scan = all_tasks()
            chain = _relation_cycle_path(base, other, kind, _scan)
            if chain:
                cycle_warnings.append(
                    "warning: this creates a %s cycle %s. Stored anyway."
                    % (kind, " → ".join("#%s" % s for s in chain)))
    # An absorbed task's children are NAMED in the handoff — never moved. Read before
    # the write, since the reconcile has to know what is now orphaned.
    if edge_targets.get("absorbed-by"):
        if _scan is None:
            _scan = all_tasks()
        absorb_children = {c.get("seq") for c in _parent_children(base, _scan)}

    cap = {}

    def _apply(task):
        msgs = list(relate_warnings)
        changed = []
        # THE UNDO TRAIL for the two verbs that write IMMEDIATELY. `--supersedes` and
        # `--step-supersede` never pass through `heal --apply`, so they take no backup —
        # and since `/heal` stopped asking for approval before it acts, they are the
        # writes with nothing standing in front of them at all. Each successful one
        # records the exact command that takes it back, with the index it really touched;
        # they are printed together below the result line. Every other flag here either
        # names its own reversal already (`--summary` → `--restore-summary`) or is a
        # plain field write with nothing to reverse.
        undos = []
        # Typed-edge targets that earned a post-save notice: a replaced parent, and an
        # absorb (whose reconcile handoff is REQUIRED output, not decoration).
        parent_notes, absorb_notes = [], []
        if a.title is not None:
            task["title"] = a.title.strip(); changed.append("title")
        # `--summary` REPLACES wholesale, and that replacement used to be the one
        # DESTRUCTIVE write left in this codebase: supersede, split, merge and
        # step-supersede all keep the original and offer a restore, while the summary —
        # the FIRST field a resuming session reads — could be lost outright to a thin
        # save. The previous text is now preserved append-only BEFORE the overwrite. An
        # unchanged rewrite is not a version (nothing was lost) and neither is replacing
        # a blank summary (there was nothing to keep).
        if a.summary is not None:
            new_summary = a.summary.strip()
            prev_summary = (task.get("summary") or "").strip()
            version = (_save.push_summary(task, prev_summary, sid=session)
                       if prev_summary and prev_summary != new_summary else None)
            task["summary"] = new_summary; changed.append("summary")
            add_event(task, "summary", "summary rewritten", session)
            if version:
                msgs.append("update %s: the previous summary (%d chars) was PRESERVED as "
                            "version %d — `update --task %s --restore-summary` puts it "
                            "back (`--restore-summary <n>` for an older one), and "
                            "`/todo %s history` lists every version. Nothing was lost."
                            % (ref, len(prev_summary), version, ref, ref))
        if a.append_summary:
            prev = (task.get("summary") or "").rstrip()
            add = a.append_summary.strip()
            task["summary"] = (prev + "\n" + add) if prev else add
            changed.append("summary+")
        # --restore-summary [n]: the inverse of the replace above, and the reason the
        # replace is safe. Bare = the most recent preserved version, because "that save
        # wrote a thin summary, put the good one back" is the overwhelmingly common case
        # and looking a number up first is a step between someone and undoing a loss.
        restore_sum = getattr(a, "restore_summary", None)
        if restore_sum is not None:
            wanted = str(restore_sum).strip()
            kept_before = len(_save.summary_versions(task))
            ok, err, v = _save.restore_summary(task, wanted or None, sid=session)
            if ok:
                changed.append("summary↺")
                add_event(task, "summary", "summary restored to version %d" % v, session)
                kept_now = len(_save.summary_versions(task))
                # Only claim the restore preserved something when it DID: restoring over
                # a blank summary replaces nothing, and saying otherwise would hand the
                # reader a version number that does not exist.
                msgs.append("update %s: restored summary version %d%s"
                            % (ref, v,
                               ("; the text it replaced is itself kept as version %d, so "
                                "the restore is reversible too." % kept_now)
                               if kept_now > kept_before
                               else " (the summary it replaced was empty, so there was "
                                    "nothing to preserve)."))
            else:
                msgs.append("update %s: %s" % (ref, err))
        state = getattr(a, "state", None)
        # Whether the state TEXT actually moved — not merely whether --state was passed.
        # Re-writing the identical line does not make a stale NEXT fresher, and stamping
        # it as if it did would let a task keep one alive by copying it forward.
        state_changed = False
        if state is not None:
            # The model-curated "where it stands / next step" briefing line. Distinct
            # from summary (what the task IS); blank clears it. No LLM — the model in
            # the loop maintains it as it works.
            state_changed = state.strip() != (task.get("state") or "").strip()
            task["state"] = state.strip()
            changed.append("state")
        goal = getattr(a, "goal", None)
        if goal is not None:
            # One-line "what done looks like." Blank clears it.
            goal_moved = goal.strip() != (task.get("goal") or "").strip()
            task["goal"] = goal.strip()
            changed.append("goal")
            if goal_moved:
                # Baseline for heal's GOAL REVIEW: the moment, plus how many decisions
                # the log held then. Without it that section can only say "cannot be
                # counted" — and it must never say "0", which reads as "nothing has
                # happened since" when the truth is "nobody recorded when this was
                # written". Stamped only when the TEXT actually moved, the same rule
                # `state_changed` above follows: re-writing the identical line does not
                # make a goal that reality has overtaken any fresher, and re-baselining
                # on a no-op write would hide exactly the drift this measures. A --goal
                # and a --decision in the SAME call snapshot before that decision lands,
                # so it reads as one already accrued — an over-count of one, in the
                # direction that prompts a look rather than the one that hides it.
                _heal.stamp_goal_touched(task)
        # Granular step ops (stable 1-based indices). --step-add appends; --step-done/
        # --step-undone tick/untick by number; an out-of-range index warns, never crashes.
        # `last_step_idx` is what --step-supersede links to, exactly as
        # `last_decision_idx` is what --supersedes links to below.
        last_step_idx = None
        for text in (getattr(a, "step_add", None) or []):
            if append_step(task, text):
                changed.append("step+")
                last_step_idx = len(task.get("steps") or [])
        task_steps = task.get("steps") or []
        # Every step refusal reads `ignoring <flag> <n> — <why>`: the flag and number
        # first (so the reader sees WHICH op was dropped), then the reason from
        # `steps.py`, which now distinguishes "no such step" from "that step is
        # superseded and off the active checklist".
        for n in (getattr(a, "step_done", None) or []):
            ok, err = _steps.set_done(task_steps, n, True)
            if ok:
                changed.append("step✓")
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        for n in (getattr(a, "step_undone", None) or []):
            ok, err = _steps.set_done(task_steps, n, False)
            if ok:
                changed.append("step☐")
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        # --step-supersede <n>: the checklist's ONE reconcile verb, shaped exactly like
        # the decision one. A step that has gone stale had no honest exit before this —
        # ticking it done is a lie, deleting it destroys the record, and a "do not
        # execute step 3" warning step is the anti-pattern. Non-destructive: the step
        # leaves the checklist and both sides of the n/m counter, keeps its text in
        # `/todo <n> history` marked with what replaced it, and --step-restore undoes it.
        # A --step-add in the SAME update is recorded as the replacement. There is
        # deliberately no --step-edit: rewriting a step in place mutates the record.
        for n in (getattr(a, "step_supersede", None) or []):
            ok, err = _steps.mark_superseded(task_steps, n, last_step_idx)
            if ok:
                changed.append("step⊘")
                undos.append("retired step %s%s → `update --task %s --step-restore %s`"
                             % (n,
                                (" (replaced by step %d)" % last_step_idx)
                                if last_step_idx else "",
                                ref, n))
                add_event(task, "step",
                          "step %s superseded%s"
                          % (n, (" by step %d" % last_step_idx) if last_step_idx else ""),
                          session)
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        for n in (getattr(a, "step_restore", None) or []):
            ok, err = _steps.restore(task_steps, n)
            if ok:
                changed.append("step↺")
                add_event(task, "step", "step %s restored to the checklist" % n, session)
            else:
                msgs.append("update %s: ignoring %s" % (ref, err))
        # --decision, plus the supersession/pin primitive. `--supersedes <n>` (repeatable)
        # and `--pin` attach to the LAST --decision appended in THIS update — one new
        # decision may replace several old ones. Every failure is a loud message, never a
        # silent no-op: a dropped supersession leaves the wrong decision live, which is
        # precisely the bug. `--pin-decision`/`--unpin-decision` act on existing entries.
        last_decision_idx = None
        for text in (getattr(a, "decision", None) or []):
            if append_decision(task, text, session):
                changed.append("decision")
                last_decision_idx = len(task.get("decisions") or [])
                # LENGTH ADVISORY — a suggestion, never a gate. The entry is already
                # stored, in full: refusing a long decision would push the author to
                # drop a fact or fake two entries out of one, which is exactly what
                # the old pin cap's refusal produced.
                warn = _dec.length_warning(task["decisions"][last_decision_idx - 1],
                                           last_decision_idx)
                if warn:
                    msgs.append("update %s: %s" % (ref, warn))
        # NOT setdefault — a task with no decisions must not grow the field just because
        # a bad --supersedes was passed; an empty list makes every index check fail loudly.
        entries = task.get("decisions") or []
        supersedes = [n for n in (getattr(a, "supersedes", None) or [])]
        if supersedes and last_decision_idx is None:
            msgs.append("update %s: --supersedes needs a --decision in the same update "
                        "(the new decision is what replaces the old one)" % ref)
        else:
            for n in supersedes:
                ok, err = _dec.mark_superseded(entries, n, last_decision_idx)
                if ok:
                    changed.append("supersede")
                    undos.append("superseded decision %s (by decision %s) → `update "
                                 "--task %s --restore-decision %s`"
                                 % (n, last_decision_idx, ref, n))
                    add_event(task, "decision",
                              "decision %s superseded by decision %s" % (n, last_decision_idx),
                              session)
                else:
                    msgs.append("update %s: %s" % (ref, err))
        if getattr(a, "pin", False):
            if last_decision_idx is None:
                msgs.append("update %s: --pin needs a --decision in the same update "
                            "(to pin an existing one use --pin-decision <n>)" % ref)
            else:
                ok, err = _dec.set_pin(entries, last_decision_idx, True, flag="--pin")
                if ok:
                    changed.append("pin")
                else:
                    msgs.append("update %s: %s" % (ref, err))
        for n in (getattr(a, "pin_decision", None) or []):
            ok, err = _dec.set_pin(entries, n, True)
            if ok:
                changed.append("pin")
            else:
                msgs.append("update %s: %s" % (ref, err))
        for n in (getattr(a, "unpin_decision", None) or []):
            ok, err = _dec.set_pin(entries, n, False)
            if ok:
                changed.append("unpin")
            else:
                msgs.append("update %s: %s" % (ref, err))
        # --restore-decision <n>: the ONE inverse of all three reconcile verbs
        # (supersede / split / merge), which is what makes each of them reversible.
        # Reads the label BEFORE restoring so the result line can say what it undid.
        for n in (getattr(a, "restore_decision", None) or []):
            label = None
            try:
                label = _dec.replacement_label(entries[int(n) - 1])
            except (TypeError, ValueError, IndexError):
                pass
            ok, err = _dec.restore(entries, n)
            if ok:
                changed.append("restore")
                add_event(task, "decision",
                          "decision %s restored (was %s)" % (n, label or "replaced"),
                          session)
            else:
                msgs.append("update %s: %s" % (ref, err))
        # --log: append a dated milestone/finding to the append-only `history` trail.
        # Off the default resume path — surfaced only by `/todo <n> history`.
        for text in (getattr(a, "log", None) or []):
            if append_history(task, text, session):
                changed.append("log")
        # --relate: record the edge on THIS task (idempotent — append_related dedupes);
        # the reciprocal `child` post on each newly-related task happens after.
        related_new = []
        for other in relate_targets:
            if append_related(task, other, "related"):
                changed.append("relate")
                related_new.append(other)
        # --- the typed edges ---------------------------------------------------
        # Every one writes on THIS task (the subordinate side stores the edge), so the
        # whole block stays inside the one optimistic-locked mutation. The result token
        # each appends to `changed` IS the reported line — `parent #503 (REPLACED #499)`
        # reads back verbatim through the "updated task N: …" join below.
        for kind in ("depends-on", "duplicates", "replaces"):
            for other in edge_targets.get(kind, []):
                if append_related(task, other, kind):
                    changed.append("%s #%s" % (kind, other.get("seq")))
        # --parent: at most ONE. A second write REPLACES the first and NEVER silently
        # swaps — a task under two parents double-counts in every roll-up, so this is a
        # tree, not a DAG.
        for other in edge_targets.get("parent", []):
            replaced = [r.get("seq") for r in (task.get("related") or [])
                        if r.get("kind") == "parent" and r.get("id") != other.get("id")]
            if replaced:
                task["related"] = [r for r in (task.get("related") or [])
                                   if not (r.get("kind") == "parent"
                                           and r.get("id") != other.get("id"))]
            added = append_related(task, other, "parent")
            if added or replaced:
                changed.append("parent #%s%s"
                               % (other.get("seq"),
                                  (" (REPLACED %s)"
                                   % ", ".join("#%s" % s for s in replaced))
                                  if replaced else ""))
                parent_notes.append(other)
        # --absorbed-by: THIS task's work became part of the other one, so THIS task
        # closes — which is what keeps it a single-task write, no cross-task write in
        # either direction. Steps are NOT merged and children are NOT moved; the
        # reconcile handoff printed below is the deliverable, not decoration.
        for other in edge_targets.get("absorbed-by", []):
            was = task_status(task)
            added = append_related(task, other, "absorbed-by")
            shut = close_task_inplace(
                task, note="absorbed by #%s" % other.get("seq"), session=session)
            if added or shut:
                changed.append("absorbed-by #%s%s"
                               % (other.get("seq"),
                                  (" · task CLOSED (was %s)" % status_display(was))
                                  if shut else " · task was already closed"))
                absorb_notes.append((other, shut))
        # --unrelate: the ONE removal verb. Reports what went; removing nothing is a
        # plain report, not an error.
        for other in unrelate_targets:
            gone = remove_related(task, other.get("id"))
            if gone:
                changed.append("unrelated #%s (removed: %s)"
                               % (other.get("seq"), ", ".join(gone)))
            else:
                msgs.append("update %s: no edge to #%s" % (ref, other.get("seq")))
        # --pr [--pr-desc]: the desc applies to the url(s) given in the SAME update;
        # a --pr-desc with NO --pr applies to the most-recent stored pr instead.
        pr_urls = getattr(a, "pr", None) or []
        pr_desc = getattr(a, "pr_desc", None)
        for url in pr_urls:
            if add_pr(task, url, pr_desc or ""):
                changed.append("pr")
        if pr_desc is not None and not pr_urls:
            if set_pr_desc(task, pr_desc):
                changed.append("pr")
        # --story [--story-desc]: mirrors --pr exactly.
        story_urls = getattr(a, "story", None) or []
        story_desc = getattr(a, "story_desc", None)
        for url in story_urls:
            if add_story(task, url, story_desc or ""):
                changed.append("story")
        if story_desc is not None and not story_urls:
            if set_story_desc(task, story_desc):
                changed.append("story")
        tvis = getattr(a, "trail_visibility", None)
        if tvis is not None and task.get("trail_visibility") != tvis:
            task["trail_visibility"] = tvis
            changed.append("trail-visibility")
        if a.color is not None and cats:
            task["color"] = cat_color(a.color); changed.append("color")
        if a.effort is not None:
            e = normalize_effort(a.effort)
            if e is None:
                msgs.append("update: ignoring --effort %r — use xs/s/m/l/xl (or 1–5)." % a.effort)
            else:
                task["effort"] = e; changed.append("effort")
        # WRITE-TIME SNAPSHOTS, taken LAST — after this update's own --decision / --log
        # appends have landed. Taken any earlier, a summary written in the same call as
        # three decisions would immediately read as three decisions out of date, and the
        # staleness check would fire on the freshest summary the task has ever had.
        if a.summary is not None:
            _save.mark_summary_written(task)
        if state_changed:
            _save.mark_state_written(task)
        # THE CHECKPOINT STAMP. `last_full_save_ts` means "a full structured checkpoint
        # was CAPTURED" — the thing that tells one apart from a lighter --state refresh —
        # and it used to be written when the [SAVE] block was PRINTED, so a session that
        # ran /save and wrote nothing left a task claiming a checkpoint with an empty
        # summary. That is heal's zero-operation `--apply` bug one layer earlier. The
        # stamp now belongs to the WRITE, and is INFERRED from the pair that IS a
        # checkpoint rather than declared by a flag nobody has to honour.
        if _save.is_checkpoint_write(a.summary, getattr(a, "state", None)):
            _save.stamp_checkpoint(task)
            changed.append("checkpoint")
        if changed:
            clear_provisional(task)   # the model refined it → real work, not a throwaway
            # A refresh of the structured digest clears the staleness flag — the digest
            # is current again, so the opt-in Stop nudge won't re-fire until new work lands.
            if {"goal", "state", "step+", "step✓", "step☐", "step⊘", "step↺",
                    "decision", "log", "supersede", "pin", "unpin"} & set(changed):
                clear_digest_dirty(task)
            # register=False: a scope update from any session must NOT enroll that session
            # as a worker on this task, but the log event is still attributed to it.
            touch(task, session=session, note="scope updated: " + ", ".join(changed),
                  register=False)
        cap["changed"], cap["msgs"], cap["related_new"] = changed, msgs, related_new
        cap["undos"] = undos
        cap["parent_notes"], cap["absorb_notes"] = parent_notes, absorb_notes

    updated = mutate(tid, _apply)
    if updated is None:
        return "update: no task matching %r" % ref
    changed, msgs, related_new = cap["changed"], cap["msgs"], cap["related_new"]
    undos = cap.get("undos") or []
    label = updated.get("seq", updated["id"][:8])
    # Cycle warnings ride out even when nothing changed (a re-run of an already-stored
    # edge still describes a cycle the reader should know about).
    msgs.extend(cycle_warnings)
    if not changed:
        msgs.append("update %s: nothing to change (pass --title/--summary/--append-summary/"
                    "--restore-summary/--goal/--state/--step-add/--step-done/--step-undone/"
                    "--step-supersede/--step-restore/--decision/--supersedes/--pin/"
                    "--pin-decision/--unpin-decision/--restore-decision/--log/--relate/"
                    "--depends-on/--parent/--absorbed-by/--replaces/--duplicates/"
                    "--unrelate/--pr/--pr-desc/--story/--story-desc/--color/--effort)" % label)
        return "\n".join(msgs)
    # Reciprocal `child` post on each newly-related task — a side effect, so it runs
    # AFTER the self-update, each write itself optimistic-locked.
    for other in related_new:
        mutate(other["id"], lambda o, seq=updated.get("seq"), title=updated.get("title"):
               add_event(o, "child", "related ← #%s: %s" % (seq, title), session))
    # --replaces closes the TARGET — the opposite direction from --absorbed-by, which
    # closes the task being updated. That makes it the one typed edge with a cross-task
    # write, so it runs HERE (after the atomic self-update) and is itself
    # optimistic-locked, exactly like --relate's reciprocal posts above. Its notices are
    # collected rather than appended, so they print under the result line, not above it.
    replace_notes = []
    for other in edge_targets.get("replaces", []):
        was, shut = task_status(other), {"v": False}

        def _shut(o, _seq=updated.get("seq"), _title=updated.get("title"), _f=shut):
            _f["v"] = close_task_inplace(o, note="replaced by #%s" % _seq, session=session)
            add_event(o, "child", "replaced by #%s: %s" % (_seq, _title), session)
        target = mutate(other["id"], _shut)
        if target is None:
            continue
        if shut["v"]:
            # NO reconcile notice, and that is the point: replacing says the approach was
            # dropped, so nothing was inherited and nothing needs recalculating. The
            # asymmetry with --absorbed-by is the whole reason both verbs exist.
            replace_notes.append("  ↳ #%s CLOSED (was %s) — its approach was replaced, so "
                                 "no work carried over." % (other.get("seq"),
                                                            status_display(was)))
            _obsidian_event(target, "closed")
            _stream_emit("task.status", target,
                         {"status": target.get("status"),
                          "closed_ts": target.get("closed_ts")}, session)
        else:
            replace_notes.append("  ↳ #%s was already closed." % other.get("seq"))
    # F6.2: a manually-added PR/story signal triggers the same cross-person auto-link as
    # capture — pair with any peer task carrying the same signal (idempotent). Only when
    # this update actually touched a signal, so a pure --title edit does no feed scan.
    if {"pr", "story"} & set(changed):
        try:
            if _autolink_task_signals(updated, session):
                updated["updated_ts"] = _now()
                save_task(updated)
        except Exception:
            pass
    _obsidian_sync(updated)
    # Stream: a scope change is task.updated; each new --relate edge is its own
    # task.relation (so a pure --relate emits exactly one relation, no task.updated).
    if [c for c in changed if c != "relate"]:
        _stream_emit("task.updated", updated, _stream_updated_data(updated, changed), session)
    for other in related_new:
        _stream_emit("task.relation", updated,
                     {"kind": "related",
                      "other": {"uuid": other.get("uuid") or other.get("id"),
                                "seq": other.get("seq")}}, session)
    task = updated                       # post-save notices below read the saved task
    msgs.append("updated task %s: %s" % (label, ", ".join(changed)))
    if undos:
        # Printed on the WRITE, not left to a skill to remember. These two verbs land
        # immediately, with no backup and — since `/heal` stopped asking before it acts —
        # nothing standing in front of them, so the report that says a mark was made is
        # the right place to say how to take it back.
        msgs.append("  UNDO — each mark above and the ONE command that reverses it:")
        msgs.extend("    • %s" % u for u in undos)
        msgs.append("    Nothing was deleted: the replacement stays on the record either "
                    "way, and a restore puts the original back BESIDE it.")
    msgs.extend(replace_notes)
    # --parent: warn when the NEW parent carries an authored `state`. A task with
    # children has a COMPUTED state, which authors cannot write — so say now that the
    # hand-written line is going to be replaced rather than folded in.
    for other in (cap.get("parent_notes") or []):
        if (other.get("state") or "").strip():
            msgs.append("note: #%s now has children — its state becomes DERIVED when "
                        "computed state ships;\n      the current hand-written state "
                        "will be replaced, not merged." % other.get("seq"))
    # --absorbed-by: the REQUIRED handoff. Absorbing is a reconcile, not a mechanical
    # move: a survivor whose checklist is the blind union of two plans describes work
    # nobody intends to do. So steps are never merged, children are never moved, and
    # nothing is written on the survivor at all (absorb stays a single-task write) —
    # this notice is the entire deliverable on that side.
    for other, shut in (cap.get("absorb_notes") or []):
        if not shut:
            continue
        osq = other.get("seq")
        msgs.append("RECONCILE NEEDED on #%s: absorbing inherits work, so #%s's steps "
                    "must be recalculated —\n  some of #%s's are already done there, "
                    "some are now redundant, some conflict.\n"
                    "  Run: task-station heal --task %s" % (osq, osq, label, osq))
        kids = sorted(s for s in absorb_children if s is not None)
        if kids:
            msgs.append("  #%s's children were NOT moved and are now orphaned: %s — where "
                        "each belongs is part of the reconcile, not a mechanical reparent."
                        % (label, ", ".join("#%s" % s for s in kids)))
        _obsidian_event(updated, "closed")
        _stream_emit("task.status", updated,
                     {"status": updated.get("status"),
                      "closed_ts": updated.get("closed_ts")}, session)
    # THE COLD-READ CHECK, MECHANICAL. It used to be advice — "re-read the digest as if
    # you have no memory of this conversation" — which is unfalsifiable: no output ever
    # said whether it was done. Two of its conditions are decidable, so the machine
    # decides them, here, on the write that claims the checkpoint. It costs nothing and
    # needs no extra call; `/todo <n> save --check` re-runs the same check on demand.
    if "checkpoint" in changed:
        msgs.append("  CHECKPOINT STAMPED — this task now records that a FULL structured "
                    "checkpoint was captured (a --summary and a --state written "
                    "together). A --state refresh alone does not stamp, which is what "
                    "keeps the two tellable apart.")
        fails = _save.cold_read_failures(task)
        if fails:
            msgs.append("  COLD-READ CHECK: %d condition(s) still FAIL — patch each with "
                        "another `update` before you finish:" % len(fails))
            msgs.extend("    • %s" % f for f in fails)
        else:
            msgs.append("  COLD-READ CHECK: pass — every named slot carries something and "
                        "`state` leads with `NEXT:`.")
    if "color" in changed and cats and hasattr(cats, "auto_enable"):
        notice = cats.auto_enable(task.get("color"))
        if notice:
            msgs.append("  ↳ " + notice)
    if "color" in changed:
        _emit_tint_to_origin(task.get("color"))   # recategorize tints NOW, not next prompt
    if "title" in changed:
        _emit_title_to_origin(task)               # a rename relabels the window NOW, not next prompt
    # A scope change is the moment effort might have grown or shrunk — prompt a
    # re-rate so the column tracks reality, but only when this update touched
    # scope WITHOUT already re-rating (so re-setting effort itself stays quiet).
    if {"title", "summary", "summary+"} & set(changed) and "effort" not in changed:
        cur = task.get("effort")
        shown = ("currently %s %s" % (EFFORT_GAUGE[cur], cur)) if cur in EFFORT_GAUGE else "currently unset"
        msgs.append("  ↳ scope changed (%s). If the work now looks bigger or smaller, re-rate:\n"
                    "      task-station update --task %s --effort <xs|s|m|l|xl>"
                    % (shown, label))
    return "\n".join(msgs)


def cmd_update(a):
    """Amend a task's title / summary / scope / colour after creation.

    Fills the gap that `summary` was otherwise frozen at create — keeps the task
    description current as scope drifts. `--task` accepts a comma-separated list:
    the same flags are applied to each task, one result line per ref, a bad ref
    reported but not aborting the rest."""
    refs = _split_refs(a.task)
    if not refs:
        sys.stderr.write("update: no task given\n")
        return
    for r in refs:
        print(_update_one(r, a))


def _pin_one(ref, a):
    """Pin session `a.session` as the resume target for the task named by `ref`
    and return its result line. A bad ref returns a no-match line (never raises)
    so a comma list keeps going past it."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "pin: no task matching %r" % ref
    # `pin --new`: pre-bind an UNBORN session as the pin. Mints a uuid, records it
    # (preborn) + links it, and emits `claude --session-id <uuid>` so opening it
    # BECOMES the task's session — bypassing the stale-pin "no transcript" guard
    # for this intentional case.
    if getattr(a, "new", False):
        sid, cmd = fresh_resume_command(task, preborn=True)
        task["pinned_session"] = sid
        touch(task, note="pinned a fresh (unborn) session %s" % sid[:8])
        save_task(task)
        label = task.get("seq", task["id"][:8])
        return ("Pinned task %s → fresh session %s (unborn — opens on first launch)\n"
                "  resume: %s" % (label, sid[:8], cmd))
    if not a.session:
        return "pin: task %s needs --session <id> or --new" % task.get("seq", task["id"][:8])
    task["pinned_session"] = a.session
    meta = task.setdefault("session_meta", {})
    if a.session not in meta:
        path = _find_session_path(a.session)
        meta[a.session] = {"cwd": (_session_cwd(path) if path else None) or os.getcwd(),
                           "ts": _now(), "role": "hub"}
    touch(task, note="pinned resume session %s" % a.session[:8])
    save_task(task)
    label = task.get("seq", task["id"][:8])
    if _find_session_path(a.session):
        return ("Pinned task %s → session %s\n  resume: %s"
                % (label, a.session[:8], resume_command(task)))
    return ("Pinned task %s → session %s — note: no transcript found for that id yet; "
            "/todo falls back to the heuristic until it appears." % (label, a.session[:8]))


def cmd_pin(a):
    """Pin a specific session as the task's canonical resume target (PK-style).

    `/todo` then always resumes THIS session, overriding the most-recent-substantive
    heuristic — the cwd is still read live from the transcript, so the pin survives
    directory changes. A pin with no findable live transcript is ignored (falls back
    to the heuristic) so it can't strand you. `--task` accepts a comma-separated
    list (pin the session across several tasks), one result line per ref.

    With NO --task but a --session given (the bare `/pin`), pin THIS session to its
    OWN attached task — resolved via get_link(session) — so /todo always resumes here.
    With neither, the usual "no task given" stderr line."""
    refs = _split_refs(a.task)
    if not refs:
        # bare /pin: pin this session to the task it's attached to.
        if getattr(a, "session", None):
            task_id = get_link(a.session)
            task = load_task(task_id) if task_id else None
            if not task:
                print("No task is attached to this session — nothing to pin.")
                return
            print(_pin_one(str(task.get("seq") or task["id"]), a))
            return
        sys.stderr.write("pin: no task given\n")
        return
    for r in refs:
        print(_pin_one(r, a))


def _unpin_one(ref):
    """Drop the pinned resume session on the task named by `ref` and return its
    result line. A bad ref returns a no-match line (never raises)."""
    task = resolve_ref(ref) or load_task(ref)
    if not task:
        return "unpin: no task matching %r" % ref
    if task.pop("pinned_session", None):
        touch(task, note="unpinned resume session")
        save_task(task)
        return ("Unpinned task %s — resume reverts to most-recent-substantive."
                % task.get("seq", task["id"][:8]))
    return "Task %s was not pinned." % task.get("seq", task["id"][:8])


def cmd_unpin(a):
    """Drop a task's pinned resume session — revert to most-recent-substantive.

    `--task` accepts a comma-separated list (unpin several at once), one result
    line per ref, a bad ref reported but not aborting the rest.

    With NO --task but a --session given (the bare `/unpin`), unpin the task THIS
    session is attached to — resolved via get_link(session) — the symmetric inverse
    of the bare `/pin`. With neither, the usual "no task given" stderr line."""
    refs = _split_refs(a.task)
    if not refs:
        # bare /unpin: unpin the task this session is attached to.
        if getattr(a, "session", None):
            task_id = get_link(a.session)
            task = load_task(task_id) if task_id else None
            if not task:
                print("No task is attached to this session — nothing to unpin.")
                return
            print(_unpin_one(str(task.get("seq") or task["id"])))
            return
        sys.stderr.write("unpin: no task given\n")
        return
    for r in refs:
        print(_unpin_one(r))


def cmd_prompt_tint(a):
    """Emit the full-palette tint escape for the skill a prompt invokes (or
    nothing), for the detected terminal (zero-setup OSC; see
    categories.tint_escape). The UserPromptSubmit hook calls this and writes
    whatever it prints to the originating TTY — so a skill like /review tints the
    terminal the instant it's run, before Claude responds. Silent when tinting is
    off, categories are off, the prompt isn't a skill, or the skill has no mapping."""
    import config
    if not config.tint_enabled():
        return
    if not cats or not hasattr(cats, "color_for_prompt") or not cats.TINT_TERMINAL:
        return
    prompt = a.prompt if getattr(a, "prompt", None) is not None else os.environ.get("TASK_STATION_PROMPT", "")
    color = cats.color_for_prompt(prompt)
    if not color:
        # The prompt invokes no skill → fall back to the ATTACHED task's category
        # colour (like cmd_session_tint), so a plain `/todo <n>` — or any non-skill
        # prompt — repaints the CURRENT window to the active task's theme tint
        # instead of leaving it on whatever the last skill painted.
        # EXCEPT a `/todo <n> -s` session-jump: that opens the task in a NEW window
        # and must leave the invoking window's tint alone — never repaint it to the
        # jumped task (v1.9.1; belt-and-suspenders to _jump_one not linking here).
        if _is_session_jump_prompt(prompt):
            return
        session = getattr(a, "session", None)
        task_id = get_link(session) if session else None
        if task_id and task_id != SKIP_SENTINEL:
            task = load_task(task_id)
            if task and task.get("color"):
                color = task.get("color")
    if not color:
        return
    import config, term
    esc = cats.tint_escape(color, term.detect())
    if esc:
        sys.stdout.write(esc)


def cmd_session_tint(a):
    """Emit the full-palette tint escape for the ATTACHED task's category, so the
    terminal tints on attach/resume (not only on the first prompt). Mirrors
    prompt-tint but resolves the colour from the session's task instead of the
    prompt. Silent when tinting is off, the session is unattached/skipped, or the
    task carries no colour; the SessionStart hook writes the bytes to the TTY."""
    import config
    if not config.tint_enabled():
        return
    if not cats or not getattr(cats, "TINT_TERMINAL", False):
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task or not task.get("color"):
        return
    import config, term
    esc = cats.tint_escape(task.get("color"), term.detect())
    if esc:
        sys.stdout.write(esc)


def _emit_tint_to_origin(color):
    """Best-effort: paint the originating window the moment a colour is assigned
    (create / attach / recategorize) instead of waiting for the next prompt.

    The prompt-tint / session-tint hooks emit the escape to stdout and the shell
    hook redirects it to the origin TTY; but a `create`/`attach` command runs in
    Claude's captured Bash tool, whose stdout is the model-visible RESULT, not the
    terminal — so writing the escape to stdout here would corrupt that result and
    never reach the window. Instead we resolve the origin TTY ourselves (same
    `origin-tty.sh` the hooks use) and write the bytes straight to it.

    Pure best-effort: a no-op (never raises, never writes to stdout) when tinting
    is off, categories/tint are unavailable, no colour, or the TTY can't be
    resolved — the next UserPromptSubmit hook still tints as it did before."""
    if not color:
        return
    if not cats or not getattr(cats, "TINT_TERMINAL", False):
        return
    import config, term
    if not config.tint_enabled():
        return
    esc = cats.tint_escape(color, term.detect())
    if not esc:
        return
    try:
        dev = subprocess.check_output(
            ["bash", os.path.join(BASE, "origin-tty.sh")],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        dev = ""
    if not dev:
        return
    try:
        with open(dev, "w") as fh:
            fh.write(esc)
    except Exception:
        pass   # unwritable/vanished TTY — the prompt hook will tint next message


def _emit_title_to_origin(task):
    """Best-effort: relabel the originating window `#<seq>: <title>` the moment a
    task is created / attached / renamed, instead of waiting for the next prompt.

    Same rationale + mechanism as _emit_tint_to_origin: a create/attach/update
    command runs in Claude's captured Bash tool (stdout = the model-visible
    result, not the terminal), so we resolve the origin TTY ourselves (the same
    `origin-tty.sh` the hooks use) and write the OSC-0 escape straight to it.
    Pure best-effort: a no-op (never raises, never writes stdout) when the title
    feature is off or the TTY can't be resolved — the UserPromptSubmit hook still
    relabels the window next message."""
    if not task:
        return
    import config
    if not config.title_enabled():
        return
    ensure_seqs()
    esc = "\033]0;#%s: %s\007" % (task.get("seq", "?"), task.get("title", ""))
    try:
        dev = subprocess.check_output(
            ["bash", os.path.join(BASE, "origin-tty.sh")],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        dev = ""
    if not dev:
        return
    try:
        with open(dev, "w") as fh:
            fh.write(esc)
    except Exception:
        pass   # unwritable/vanished TTY — the prompt hook will relabel next message


def cmd_prompt_title(a):
    """Emit an OSC title escape that labels the terminal tab/window `#<seq>: <title>`
    for an attached session — the on-attach surface, run by UserPromptSubmit every
    prompt. Pure stdout (like prompt-tint); the hook delivers the bytes to the real
    terminal. Emits NOTHING when the title feature is off (config / `TASK_STATION_TITLE=off`)
    or the session is unattached/skipped, so the user's own title is left untouched."""
    import config
    if not config.title_enabled():
        return
    task_id = get_link(a.session)
    if not task_id or task_id == SKIP_SENTINEL:
        return
    task = load_task(task_id)
    if not task:
        return
    ensure_seqs()
    # OSC 0 sets both tab and window title (Terminal.app + iTerm2); \033]0; … \007.
    sys.stdout.write("\033]0;#%s: %s\007" % (task.get("seq", "?"), task["title"]))


def _auto_track_provisional(a, prompt):
    """guaranteed-tracking: deterministically track a fresh, unattached session.

    Fold-don't-fork: if a similar OPEN task already exists, attach to it and fold
    the prompt in as a note (no sibling). Otherwise create a PROVISIONAL open task
    (auto-categorised), attach this session, and print a short directive telling
    the model how to refine it (`update`) or drop it (`skip`, which GCs it)."""
    seed = seed_title(prompt)
    note = (prompt or "").strip()

    dup = similar_open_task(seed)
    if dup:
        # F9 identity guard: a flavor-similar OPEN task is NOT a fold target when
        # the prompt names a PR/work-item the task doesn't carry (both keyed,
        # disjoint) — it's a different work item, so drop to the create path below
        # (bias create on mismatch, mirroring the interactive attach soft-guard).
        _pk = extract_identity_keys(prompt)
        if _pk and task_identity_keys(dup) and not (_pk & task_identity_keys(dup)):
            dup = None
    if dup:
        # Same code path cmd_attach uses to fold a cross-session prompt in: a
        # marker note via touch(), then the prompt itself via add_log() (so the
        # prompt is logged exactly once, not duplicated by touch's own add_log).
        touch(dup, session=a.session, note="auto-tracked (folded in)", reopen=True)
        if note:
            add_log(dup, note)
        save_task(dup)
        set_link(a.session, dup["id"])
        clear_count(a.session)
        auto_enable_category(dup.get("color"))
        _emit_tint_to_origin(dup.get("color"))   # tint NOW on auto-fold
        _emit_title_to_origin(dup)               # relabel the window NOW on auto-fold
        print("[task-station] Auto-tracked: folded into open task [%s] %s — this "
              "session is now attached and your prompt was noted. No sibling task "
              "was created." % (dup["id"][:8], dup["title"]))
        return

    color = None
    if cats:
        color = (cats.color_for_prompt(prompt) if hasattr(cats, "color_for_prompt") else None) or cats.DEFAULT
    task = new_task(seed, "", color=color, status=STATUS_OPEN)
    task["provisional"] = True
    create_with_seq(task)              # atomically mint the stable number + persist
    touch(task, session=a.session, note="auto-tracked (provisional)")
    save_task(task)
    set_link(a.session, task["id"])
    clear_count(a.session)
    auto_enable_category(task.get("color"))
    _emit_tint_to_origin(task.get("color"))   # tint NOW on provisional auto-create
    _emit_title_to_origin(task)               # label the window NOW on provisional auto-create

    tid = task["id"][:8]
    label = task.get("seq", tid)
    print("\n".join([
        "[task-station] Auto-tracked as task [%s] %s (provisional) — this session is "
        "now attached." % (tid, task["title"]),
        "If this is real work, refine it (clears the provisional flag):",
        "  update: task-station update --task %s --title '<short title>' "
        "--color <color> --summary '<1-3 sentences>'" % label,
        "If it is genuinely throwaway/meta, drop it (removes the provisional task):",
        "  skip:   task-station skip --session %s" % a.session,
        "  " + _cli_fallback(),
    ]))


def _fold_candidate_lines(prompt, opens, header):
    """Render the open-task candidate block for the fold-in / attach nudge with F9
    identity-keyed filtering.

    When the incoming `prompt` carries ≥1 identity key, candidates are limited to
    open tasks that share a key — flavor-only matches (same process words, different
    PR/work-item) are excluded, which is the whole point: attach on IDENTITY, not
    flavor. When the prompt carries a key that NO open task matches, the block is a
    single create-bias line instead of a candidate list. Keyless prompts list every
    open task exactly as before. Each candidate line renders that task's OWN keys
    (when it has any) so a mismatch is glanceable even on a keyless prompt. Keyless
    prompt + keyless tasks ⇒ byte-identical to the pre-F9 block. Returns a list of
    lines (empty when there is nothing to show)."""
    pkeys = extract_identity_keys(prompt)
    if pkeys:
        cands = [t for t in opens if task_identity_keys(t) & pkeys]
        if not cands:
            return ["No open task carries %s — this is a NEW work item; create a "
                    "task, don't fold into a flavor-only match."
                    % render_identity_keys(pkeys)]
    else:
        cands = list(opens)
    if not cands:
        return []
    lines = [header]
    for t in cands[:8]:
        ks = task_identity_keys(t)
        suffix = (" → keys: " + render_identity_keys(ks)) if ks else ""
        lines.append("  - #%s [%s] %s (%s)%s"
                     % (t.get("seq") or "?", t["id"][:8], t["title"],
                        rel_time(t.get("updated_ts")), suffix))
    return lines


def cmd_prompt_context(a):
    """UserPromptSubmit: bump if attached; otherwise nudge Claude to attach/create.

    An EXPLICIT 'create/attach a task' request in the prompt always prints a hard
    directive FIRST — even in a skipped or already-attached session — steering to
    task-station and away from the built-in/native TaskCreate session-todo tool."""
    intent = (cats.task_intent(os.environ.get("TASK_STATION_PROMPT", ""))
              if (cats and hasattr(cats, "task_intent")) else None)
    task_id = get_link(a.session)
    skipped = task_id == SKIP_SENTINEL
    task = load_task(task_id) if (task_id and not skipped) else None

    _ref_block = _resolve_prompt_task_refs(os.environ.get("TASK_STATION_PROMPT", ""))
    if _ref_block:
        print(_ref_block)

    if intent:
        verb = "attach" if intent == "attach" else "create"
        dlines = [
            "⚡ EXPLICIT TASK INTENT — the user's message explicitly asks to %s a task." % verb,
            "Track it on task-station NOW — the cross-session board, bound to THIS session for",
            "one-command resume. The native task tools are per-session/siloed (no board across",
            "sessions, no link back to the session that holds the context) — not what's wanted here.",
        ]
        if intent == "create":
            dlines.append("  create: task-station create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                          % a.session)
            if task:
                dlines.append("You are already attached to [%s] %s; if the user wants a SEPARATE task, "
                              "create with --force; if they mean this same task, you are already tracking "
                              "it — just say so." % (task["id"][:8], task["title"]))
        else:  # attach
            dlines.append("  attach: task-station attach --session %s --task <task-id> [--color <color>]"
                          % a.session)
            opens = [t for t in sorted_tasks() if is_on_board(t)]
            dlines.extend(_fold_candidate_lines(
                os.environ.get("TASK_STATION_PROMPT", ""), opens,
                "Open tasks you can attach to:"))
        print("\n".join(dlines))
        # The directive IS the message. Keep an attached task's activity fresh as
        # usual, but don't also dump the standard nudge after a hard directive.
        if task:
            touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
            save_task(task)
        return

    if skipped:
        return  # session intentionally untracked: stay silent

    if task:
        was_closed = task.get("status") == "closed"
        # Compute the delta BEFORE touch — touch rewrites this session's
        # session_meta entry (resetting its high-water mark), so the "since I last
        # looked" comparison must read the pre-touch watermark.
        delta = delta_brief(task, a.session)
        # Memo arrivals are ack-gated (not seen_ts-gated), so they re-surface every
        # turn until this session acks — computed here alongside delta, but NOT cleared
        # by touch/mark_seen (only an explicit `memo ack` clears one).
        pending = memo_pending_brief(task, a.session)
        touch(task, session=a.session, note=os.environ.get("TASK_STATION_PROMPT", ""), reopen=True)
        save_task(task)
        if was_closed:
            print("[task-station] Reopened task [%s] %s — this session is working on it again."
                  % (task["id"][:8], task["title"]))
        # MODEL steering, ONLY on an ultracode turn (the harness is already
        # orchestrating): on a fan-out-worthy task with the hints feature on and an
        # ultracode signal in THIS prompt, steer breadth to think-phases and keep
        # repo writes on the delegation path. Default mode prints NOTHING here (the
        # human advisory lives on the lower-frequency detail/SessionStart surfaces),
        # so the per-prompt cost stays zero unless ultracode is in play.
        import config
        if (config.ultracode_hints_enabled() and fanout_worthy(task)
                and ultracode_signal(os.environ.get("TASK_STATION_PROMPT", ""))):
            print(ultracode_steering())
        # Delta-injection + memo pending are the only things the attached+open path
        # may emit. When another session/worker/child advanced the feed since this
        # session last looked, surface ONE bounded delta block and advance the
        # watermark so it never repeats. No news → stay silent.
        if delta:
            print(delta)
            mark_seen(task, a.session)
            save_task(task)
        # Pending memos re-surface until acked — printed AFTER the delta, and NOT
        # watermark-cleared (mark_seen doesn't touch them; only `memo ack` does).
        if pending:
            print(pending)
        # Glossary auto-injection: when the attached task carries a canonical
        # vocabulary, append it so every session reuses the same terms. Gated —
        # emits nothing when the glossary is empty, so the per-prompt cost is zero
        # until terms exist. Claude's UserPromptSubmit wiring; other hosts emit the
        # same block via the `glossary-context` adapter hook.
        gc = glossary_context(task)
        if gc:
            print(gc)
        return  # attached & open: nothing else to emit

    # Not attached: count the miss, surface open tasks, and nudge Claude.
    n = bump_count(a.session)

    # Guaranteed-tracking (opt-in, default OFF): on the FIRST miss of a fresh,
    # unattached, non-skipped, no-explicit-intent session, the hook itself
    # deterministically creates+attaches a provisional task (fold-don't-fork) and
    # returns — no nudge. Default OFF → behaviour is exactly the firmer nudge below.
    import config
    if config.guaranteed_tracking_enabled() and n == 1:
        _auto_track_provisional(a, os.environ.get("TASK_STATION_PROMPT", ""))
        return

    # Intermediate misses (1 < n < NUDGE_ESCALATE_AFTER): a SINGLE compact line.
    # The full block — open-task list, attach/create syntax, colour legend, tint,
    # guidance pointer — was already shown at n == 1, so reprinting it every
    # message just burns tokens. Only n == 1 gets the full block (below).
    if 1 < n < NUDGE_ESCALATE_AFTER:
        line = ("[task-station] Still untracked (msg %d). Track the topic as an OPEN task "
                "(○) — or fold it into a task above with `attach --note` — else skip." % n)
        # Category auto-detection is a compiled-regex + dict lookup — effectively
        # free — so it keeps running on EVERY prompt, even the collapsed nudge. If
        # this prompt maps to a category, carry just that one hint (no legend) so a
        # later attach can still auto-categorize.
        if cats and hasattr(cats, "color_for_prompt"):
            skill_color = cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
            if skill_color:
                line += (" This prompt maps to category '%s' (%s) — use --color %s on attach."
                         % (skill_color, cats.label(skill_color), skill_color))
        print(line)
        return

    opens = [t for t in sorted_tasks() if is_on_board(t)]
    lines = ["[task-station] This session is not attached to a tracked task yet."]
    lines.extend(_fold_candidate_lines(
        os.environ.get("TASK_STATION_PROMPT", ""), opens,
        "Open tasks that may match what the user wants:"))
    lines.append("")

    if n >= NUDGE_ESCALATE_AFTER:
        lines.append("⚠ %d messages in and still untracked. Attach/create NOW if this "
                     "is real work, else skip:" % n)
        lines.append("      task-station skip --session %s" % a.session)
        lines.append("Attach/create syntax + colours: task-station guidance")
        print("\n".join(lines))
        return

    # n == 1 only: full education block (open-task list + templates + legend).
    # Compact form: full rules/examples live in `task-station.py guidance` (and the
    # SessionStart injection points there) — keep the per-prompt cost minimal.
    lines.append("Track this topic NOW as a NEW task (○) — every topic gets tracked, "
                 "plain questions and Q&A included; it shows on the board immediately and "
                 "AUTO-PROMOTES to active (●) when you act on it (edit a file, delegate, "
                 "multi-step). FIRST scan the tasks above: if this prompt continues one of "
                 "them, FOLD INTO IT — `attach --session %s --task <id> --note '<this "
                 "prompt>'` — don't create a sibling. FOLD ON IDENTITY, NOT FLAVOR: when "
                 "this prompt names a PR or work-item (e.g. PR 1115, Projectname-3166), fold ONLY "
                 "into a task carrying that SAME key — a shared topic with a DIFFERENT "
                 "PR/story is a different work item, so create a new task. Only a genuinely "
                 "new topic creates a task." % a.session)
    if cats:
        skill_color = (cats.color_for_prompt(os.environ.get("TASK_STATION_PROMPT", ""))
                       if hasattr(cats, "color_for_prompt") else None)
        if skill_color:
            lines.append("This prompt's skill maps to category '%s' (%s); terminal already tinted — "
                         "use --color %s."
                         % (skill_color, cats.label(skill_color), skill_color))
        lines.append("  attach: task-station attach --session %s --task <task-id> [--color <color>]" % a.session)
        lines.append("  create: task-station create --session %s --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % a.session)
        legend = cats.compact_legend() if hasattr(cats, "compact_legend") else ""
        if legend:
            lines.append("Colors: " + legend)
        lines.append("Tracking happens ONLY by RUNNING the create/attach command above — then "
                     "relay its result line (the '📋 Created task [..] <title>' / '📋 Attached to "
                     "task [..] <title>' the tool prints) to the user verbatim. Do NOT write your "
                     "own 'Tracking' line: a self-authored line WITHOUT running the command leaves "
                     "the session untracked on the board while telling the user otherwise. "
                     "The terminal tints to the category automatically. "
                     "Full rules: task-station guidance")
    else:
        lines.append("  attach: task-station attach --session %s --task <task-id>" % a.session)
        lines.append("  create: task-station create --session %s --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentences>'"
                     % a.session)
        lines.append("Tracking happens ONLY by RUNNING the create/attach command above — then "
                     "relay its result line (the '📋 Created task [..] <title>' / '📋 Attached to "
                     "task [..] <title>' the tool prints) to the user verbatim. Do NOT write your "
                     "own 'Tracking' line: a self-authored line WITHOUT running the command leaves "
                     "the session untracked on the board while telling the user otherwise. "
                     "Full rules: task-station guidance")
    print("\n".join(lines))


def cmd_guidance(a):
    """Full attach/create how-to, fetched on demand (kept out of the per-prompt
    injection for token economy — `prompt-context` points here)."""
    lines = ["[task-station] Every topic gets tracked from the first prompt — TRACK, don't stay silent:",
             "  - STATUS: a topic you merely raise starts NEW (○) — track it now, even a plain question.",
             "    It shows on the board immediately and AUTO-PROMOTES to ACTIVE (●) when work starts",
             "    (you edit a file in this session, delegate --worktree, or run a multi-step process).",
             "    /done then closes it. Per-task state: new (○) → active (●) → closed (✕)  "
             "(stored value stays `open`).",
             "  - FOLD, DON'T FORK: before creating, scan the board (new + active). If this prompt",
             "    continues an existing task, ATTACH to it and append the prompt as a note — no sibling.",
             "    FOLD ON IDENTITY: when the prompt names a PR/work-item (PR 1115, #1115, Projectname-3166,",
             "    AB#3166), fold ONLY into a task carrying that SAME key — a shared topic with a",
             "    DIFFERENT PR/story is a different work item ⇒ create. `attach` soft-blocks a",
             "    key-mismatched fold (re-run with --force-key to override).",
             "  - write a one-line title good enough to recognise the topic later.",
             'TRACK examples:  "how does X work?" (new), "add dark mode", "fix the auth bug"',
             "FOLD example:    a follow-up question about a task on the board → attach --note, not a new task",
             "SKIP only genuinely throwaway/meta chatter: task-station skip --session <session-id>"]
    if cats:
        lines.extend(cats.picker_lines())
        lines.append("  • Matches a task on the board → attach (FOLD IN; --note appends this prompt to its log; "
                     "--color sets/recategorizes — a key, emoji, or [TAG]):")
        lines.append("      task-station attach --session <session-id> --task <task-id> [--note '<prompt>'] [--color <color>]")
        lines.append("  • Otherwise → create with its colour and an effort estimate "
                     "(xs/s/m/l/xl — your read of the task's complexity & scope). New tasks "
                     "start as new (○); add --active to start active (●) when work has already begun:")
        lines.append("      task-station create --session <session-id> --color <color> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>' [--active]")
        if cats.TINT_TERMINAL:
            lines.append("The terminal is tinted to the task's category automatically "
                         "(full palette via terminal escapes) — nothing to run by hand.")
    else:
        lines.append("  • attach: task-station attach --session <session-id> --task <task-id>")
        lines.append("  • create: task-station create --session <session-id> --effort <xs|s|m|l|xl> --title '<short title>' --summary '<1-3 sentence summary>'")
    lines.append("Always track via task-station (attach/create above) — it lands on your "
                 "cross-session board, bound to this session for one-command resume. The native "
                 "task tools are per-session/siloed (no global board, no session-resume link).")
    lines.append("NATIVE TASKS INTEROP (read-only): Claude Code's own in-session Tasks are for "
                 "in-session orchestration; task-station is the DURABLE cross-session console. See "
                 "recent native lists with `native` (`/todo native`); when a native item is worth "
                 "tracking durably, `adopt --native <list-prefix>:<id>` promotes it to a station "
                 "task. task-station NEVER writes the native store.")
    lines.append("The confirmation is the tool's OWN result line — run create/attach, then surface "
                 "that line (it names the task #, which is the proof it's recorded). NEVER fabricate "
                 "a '📋 Tracking' line yourself: printing one without having run the command desyncs "
                 "the board from what's actually stored.")
    lines.append("DIGEST (this is how a task stays resumable): as you work, keep the structured "
                 "digest current — `update --state '<next step / where it stands>'` (refresh when "
                 "you pause or finish), tick the checklist with `--step-done N` (add new ones with "
                 "`--step-add`), record choices as you make them with `--decision '<what & why>'`, "
                 "and log dated milestones/findings with `--log '<dated milestone/finding>'` (the "
                 "append-only HISTORY trail — it does NOT load on a normal resume; read it back via "
                 "`/todo <n> history`). `--goal '<what done looks like>'` anchors it; `--pr <url>` "
                 "pins the PR and `--story <url>` pins the story/work-item. So a resume "
                 "loads a briefing, not just a transcript.")
    lines.append("CONTENT HYGIENE: `summary` is the CURRENT-SNAPSHOT description — rewrite it to the "
                 "present truth (`--summary` REPLACES it wholesale) and keep it lean, NOT a running "
                 "log. The WHY/WHEN trail lives in `--decision` (choices + rationale) and `--log` "
                 "(dated milestones/findings) — both off the normal resume path, read back via "
                 "`/todo <n> history`. (`--append-summary` still exists but don't use it for "
                 "progress notes.) A replaced summary is NOT destroyed: it is preserved append-only "
                 "and `--restore-summary [n]` brings it back, so a thin save cannot silently lose a "
                 "good one.")
    lines.append("CHECKPOINT / RESUME: `/todo save` reports the GAP — which named slots are empty or "
                 "stale, what has landed since the last checkpoint, what the digest costs — instead "
                 "of echoing the digest you already have (`/todo save --verbose` dumps it, "
                 "`/todo save --check` re-runs the gap report alone as the mechanical cold-read "
                 "check). It captures only, no pin, and it does NOT stamp: "
                 "`last_full_save_ts` is written by the `update` that carries a `--summary` AND a "
                 "`--state`, because that pair IS the checkpoint. `/todo <n>` gives the lean recap; "
                 "`/todo <n> history` shows the full decisions + log + activity trail (and every "
                 "preserved summary version); `/todo <n> -s` resumes the original session's "
                 "transcript.")
    lines.append("Command forms: use `/task-station:<name>` (todo/save/history/pin/done/config/repos) "
                 "unless you have enabled the short bare aliases with `config --bare-cmds on` (then "
                 "`/todo`, `/save`, … work directly).")
    if _auto_checkpoint_enabled():
        lines.append("AUTO-CHECKPOINT is ON: on a compaction the harness summary is stashed to "
                     "`/todo <n> history` for free — but that is a backup, not the digest. Keep the "
                     "STRUCTURED digest current (refresh `--state`, tick `--step-done`, add a "
                     "`--decision`) so a resume stays accurate; a stale digest triggers a Stop nudge "
                     "until you refresh it.")

    # COMMANDS — compact full reference (model-facing source of truth). Use these
    # exact forms instead of reinventing a command. Preferred invocation is the
    # short `task-station <command> …` shim (the plugin's bin/ is on the Bash tool
    # PATH while enabled); the absolute python3 form is the parenthetical fallback
    # for shells without bin/ on PATH.
    lines.append("")
    lines.append("Commands  (invoke as: task-station <command> …, or "
                 "python3 %s/task-station.py <command> … if the shim isn't on PATH)" % BASE)
    lines.append("Lifecycle: new ○ → active ● → closed ✕  (stored value stays `open`).  "
                 "<task> = seq number or id-prefix; "
                 "<session> = session uuid.")
    lines.extend([
        "  create  --session <s> --color <c> --effort <xs|s|m|l|xl> --title '…' --summary '…' "
        "[--goal '…'] [--step '…' …] [--active] [--no-attach|--attach] [--force]   — track a new "
        "task (attaches the session; --goal/--step seed the digest)",
        "  attach  --session <s> --task <ref> [--color <c>] [--note '…'] [--force-key]   — link "
        "session to a task (reopens if closed). FOLD-DON'T-FORK: prefer attach --note over a new "
        "create when it continues an existing task. Soft-blocks a key-mismatched fold (prompt "
        "names a PR/work-item the task doesn't carry) — --force-key overrides",
        "  detach  --session <s> [--task <ref>]   — unlink the session from its task",
        "  update  --task <ref> [--title|--summary|--append-summary|--restore-summary [N]|--goal|"
        "--state|--step-add|"
        "--step-done N|--step-undone N|--step-supersede N|--step-restore N|--decision|"
        "--supersedes N|--pin|--pin-decision N|"
        "--unpin-decision N|--restore-decision N|--log|--pr|--pr-desc|--story|--story-desc|--color|"
        "--effort]   — amend a task / keep its digest current (--goal what-done-looks-like · "
        "--state next-step · a --summary AND a --state in ONE call STAMPS a full checkpoint "
        "(the pair IS the checkpoint — no flag declares it) · --summary REPLACES wholesale but "
        "PRESERVES what it overwrote, and --restore-summary [N] brings any version back · "
        "--step-* checklist · "
        "--step-supersede N retires a STALE step: it leaves the checklist and BOTH sides of "
        "the n/m count, keeps its text in history marked with what replaced it (a --step-add "
        "in the same call), and --step-restore N undoes it — there is no step EDIT, because "
        "rewriting a step in place mutates the record · --decision append-only · "
        "--supersedes N marks decision N REPLACED by this one (gone from the digest, kept "
        "in history) · --pin sorts a decision FIRST in the digest (ordering, not "
        "visibility — every current decision renders) · "
        "--restore-decision N UNDOES a supersede/split/merge mark (every reconcile is "
        "reversible; nothing is ever deleted) · --log dated history "
        "(off the resume path; see /todo <n> history) · --pr stored PR url · "
        "--story stored story/work-item url)",
        "  heal  [--task <ref>] [--scan] [--apply [--verbose]] [--all] [--mark-healed "
        "[--note '…']] "
        "[--dispose-acks <id8,…|all> --decision '…'|--memory <slug>|--noop '<reason>']   — "
        "RECONCILE the append-only "
        "decision log into current state (the counterpart to `save`'s capture). PREFER the "
        "`heal` SKILL, which drives the whole sequence — scan, read the dry run ONCE, "
        "propose a plan, confirm, execute, stamp — so no flag below needs typing by hand. "
        "--scan is "
        "the deterministic zero-token pass and never modifies the task (and never stamps a "
        "heal); bare `heal` is a "
        "DRY RUN that prints the plan and changes nothing; --apply performs the mechanical "
        "plan after backing the task blob up, prints ONLY what it did (--verbose for the "
        "full block), STAMPS the heal when it performed at least one operation, and is "
        "REFUSED when it would perform none rather than recording a heal that never "
        "happened. --mark-healed records the judgement-only pass where nothing needed "
        "changing (--note says why). --dispose-acks retro-fills the dispositions of acks "
        "recorded before they were required — visibly retroactive, and it never overwrites "
        "one the acking session chose. Three decision verbs: --supersedes for what is "
        "WRONG, `heal --split N --into n1,n2` for what is COMPOUND, "
        "`heal --merge n1,n2 --into N` "
        "for what is TRUE BUT NO LONGER LOAD-BEARING; `update --step-supersede N` is the "
        "same idea for a stale STEP. No verb ever deletes a decision",
        "  status  --task <ref> [new|active]   — show/set status (new = stored open; close via done)",
        "  pin     --task <ref> [--session <s>] [--new]   (or just --session <s> to pin THIS session "
        "to its attached task)   ·   unpin --task <ref>   — pin/unpin a resume target",
        "  done    --task <ref>   (or --session <s>)   ·   skip --session <s>   — close a task · mark session untracked",
        "  whoami  --session <s>   ·   render --session <s> [--arg <ref>] [--format ascii|md]   ·   "
        "bump --session <s>   — current task · the /todo board · touch activity",
        "  search  <terms> [--open|--closed|--all] [--detail <ref>]   — ranked cross-task search "
        "(tier-1 hit list over every task's text; --detail prints one task's read-only digest). "
        "Also /todo search <terms>",
        "  board   [--open]   — write a self-contained HTML board of all tasks to <data_dir>/board.html",
        "  native   ·   adopt --native <list-prefix>:<id>   — list Claude Code's in-session native "
        "tasks (read-only) · adopt one as a durable station task",
        "  config   ·   repos   — settings board · repo index",
    ])
    lines.append("Maintenance (rarely needed — prefer done/close):")
    lines.append("  delete  --task <ref>   — HARD-delete a task (hidden from --help; lifecycle is "
                 "normally close-not-delete)")

    print("\n".join(lines))


def _repos_load(repo_index, roots, data_dir):
    """Return the structured index, reading repos.json if present, else building
    it from a fresh scan (so term/--json queries work before a first --refresh)."""
    p = os.path.join(data_dir, "repos.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        # Auto-build on a read path: stay deterministic (no model calls); explicit
        # `repos --refresh` is the only place enrichment runs.
        return repo_index.build_index(roots, data_dir=data_dir, use_llm=False)


def _repos_render_manifest(repo_index, data_dir):
    """Print the full include/exclude manifest — every discovered repo + flags."""
    manifest = repo_index.load_manifest(data_dir)
    print("repo manifest  %s" % os.path.join(data_dir, "repos.config.json"))
    print("  toggle: repos include/exclude <name>  ·  repos enrich <name> [on|off]")
    print("")
    if not manifest:
        print("  (empty — run `repos --refresh` to discover repos)")
        return
    print("  index  enrich  repo")
    for name in sorted(manifest):
        e = repo_index.entry_for(manifest, name)
        print("  %-6s %-7s %s" % (
            "[x]" if e["index"] else "[ ]",
            "[x]" if e["enrich"] else "[ ]",
            name + ("" if e["index"] else "   (excluded)")))


def _repos_set_flag(repo_index, data_dir, name, key, value):
    """Set a manifest flag for `name` (or its basename if a path was given).
    Returns the resolved repo name, or None if it isn't in the manifest."""
    manifest = repo_index.load_manifest(data_dir)
    candidate = name if name in manifest else os.path.basename(os.path.normpath(name))
    if candidate not in manifest:
        return None
    entry = repo_index.entry_for(manifest, candidate)
    entry[key] = value
    manifest[candidate] = entry
    repo_index.save_manifest(data_dir, manifest)
    return candidate


def _repos_manifest_action(repo_index, data_dir, action, terms):
    """Handle the no-JSON-editing toggle subcommands. Returns True if it consumed
    the invocation."""
    if action == "config":
        _repos_render_manifest(repo_index, data_dir)
        return True
    if len(terms) < 2:
        print("usage: repos %s <name>%s" % (
            action, " [on|off]" if action == "enrich" else ""))
        return True
    name = terms[1]
    if action == "include":
        key, value, label = "index", True, "included (index:true)"
    elif action == "exclude":
        key, value, label = "index", False, "excluded (index:false)"
    else:  # enrich
        on = (terms[2].lower() != "off") if len(terms) > 2 else True
        key, value, label = "enrich", on, "enrich:%s" % ("on" if on else "off")
    resolved = _repos_set_flag(repo_index, data_dir, name, key, value)
    if resolved is None:
        print("repos: no repo named %r in the manifest. Run `repos --refresh` to "
              "discover repos, then `repos config` to list them." % name)
    else:
        print("repos: %s → %s" % (resolved, label))
        if action == "enrich" and value:
            print("       (its README + tree NAMES will be sent to the model on the "
                  "next `repos --refresh`)")
    return True


def cmd_repos(a):
    """Hub repo index: `repos [show]` prints repos.md (building it if missing),
    `repos --refresh [--quiet] [--dry-run] [--re-summarize]` rescans +
    rewrites the index, `repos <term...>` ranks matches, `--json` emits the
    structured list. Include/exclude surface: `repos config` lists the manifest;
    `repos include/exclude <name>` and `repos enrich <name> [on|off]` flip flags.
    First-run onboarding via `--detect-roots` + `--set-roots`. Not stored in
    tasks.db; lives at <data_dir>/repos.{md,json} + repos.config.json."""
    import config
    import repo_index
    data_dir = paths.data_dir()
    md_path = os.path.join(data_dir, "repos.md")
    terms = [t for t in (a.terms or []) if t != "show"]

    # --- Onboarding helpers (no scan) ---
    if getattr(a, "detect_roots", False):
        found = repo_index.detect_roots()
        if found:
            print("repos: detected candidate roots:")
            for p in found:
                print("  %s" % p)
            print("")
            print("Enrichment is OFF by default — listing a repo sends nothing to "
                  "Claude unless you turn it on per-repo with `repos enrich <name>`.")
            print("Confirm/adjust, then: repos --set-roots %s" % ",".join(found))
        else:
            print("repos: no candidate roots detected under your home directory.")
            print("Set them explicitly: repos --set-roots <p1,p2,...>")
        return
    if getattr(a, "set_roots", None) is not None:
        chosen = [p.strip() for p in a.set_roots.split(",") if p.strip()]
        config.set_repo_roots(chosen)
        print("repos: roots set → %s" % ", ".join(chosen))
        print("       Enrichment stays OFF until you opt in per repo "
              "(`repos enrich <name>`). Run `repos --refresh` to build the index.")
        return

    # --- Manifest toggle subcommands (no JSON editing) ---
    action = terms[0] if terms else None
    if action in ("include", "exclude", "enrich", "config"):
        _repos_manifest_action(repo_index, data_dir, action, terms)
        return

    roots = config.repo_roots()

    # --- First-run onboarding: no roots configured and no manifest yet ---
    if (not config.repo_roots_configured()
            and not os.path.exists(os.path.join(data_dir, "repos.config.json"))
            and not a.refresh and not terms and not a.json):
        found = repo_index.detect_roots()
        print("repos: first-run setup — no workspace roots configured yet.")
        print("")
        if found:
            print("Detected candidate roots:")
            for p in found:
                print("  %s" % p)
        else:
            print("No candidate roots auto-detected; you can name your own.")
        print("")
        print("Enrichment is OFF by default — listing a repo sends NOTHING to Claude "
              "unless you turn it on per-repo with `repos enrich <name>`.")
        print("")
        print("To proceed: confirm the roots above, then run "
              "`repos --set-roots <p1,p2,...>` followed by `repos --refresh`.")
        return

    repos = None
    if a.refresh:
        # Rescan + rewrite. Enrichment is OPT-IN: a model call fires ONLY for
        # `enrich:true` repos (and only when new/changed). A normal refresh sends
        # NOTHING off-machine. --no-llm (or the repo_enrich config gate) forces the
        # deterministic path; --dry-run reports what WOULD be sent without sending;
        # --re-summarize regenerates summaries even if cached.
        use_llm = config.repo_enrich_enabled() and not getattr(a, "no_llm", False)
        dry_run = getattr(a, "dry_run", False)
        egress = []
        repos = repo_index.build_index(
            roots, data_dir=data_dir, use_llm=use_llm, dry_run=dry_run,
            re_summarize=getattr(a, "re_summarize", False), egress=egress)
        if not a.quiet and not a.json:
            if dry_run:
                if egress:
                    print("repos: --dry-run — WOULD send README+tree for: %s "
                          "(nothing sent)" % ", ".join(sorted(egress)))
                else:
                    print("repos: --dry-run — no repos are enrich:true; nothing "
                          "would be sent.")
            elif egress:
                print("repos: enriching (sending README+tree NAMES): %s"
                      % ", ".join(sorted(egress)))
        if a.quiet and not terms and not a.json:
            sent = (" · sent: %s" % ", ".join(sorted(egress))) if egress else " · sent: nothing"
            print("repos: indexed %d repo(s) → %s%s" % (len(repos), md_path, sent))
            return

    if terms:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        q = " ".join(terms)
        hits = [r for r in repo_index.match(q, repos) if repo_index.score(q, r) > 0]
        if a.json:
            print(json.dumps(hits, indent=2, ensure_ascii=False))
        elif hits:
            print(repo_index.render_md(hits, query=q))
        else:
            print("No repos match %r." % q)
        return

    if a.json:
        if repos is None:
            repos = _repos_load(repo_index, roots, data_dir)
        print(json.dumps(repos, indent=2, ensure_ascii=False))
        return

    if repos is not None:
        # Just refreshed (non-quiet) → print what we wrote.
        print(repo_index.render_md(repos))
        return
    if not os.path.exists(md_path):
        repo_index.build_index(roots, data_dir=data_dir, use_llm=False)
    with open(md_path) as f:
        print(f.read())


def _board_session_counts(task, live_sids=None):
    """WS4: compact session-tree counts for the board card —
    `{"hubs", "workers", "running", "resumable", "live_hubs"}` using the canonical
    session-state vocabulary: `running` = hubs with a real live process (pid in
    `live_sids`); `resumable` = hubs with a findable transcript but nothing running.
    `live_hubs` is kept as the legacy transcript-findable count for older renderers.
    Prefers WS3's richer `session_tree(task)` when that sibling helper is merged;
    otherwise derives minimally from the task's own `session_meta` hubs + its
    recorded delegate workers so the board degrades gracefully when WS3 is absent.
    Never raises; a bare task (no sessions, no workers) yields all-zero counts (the
    renderer omits the row)."""
    running_sids = set(live_sids or [])
    st = globals().get("session_tree")
    if callable(st):
        try:
            tree = st(task) or {}
            hubs = tree.get("hubs") or []
            workers = sum(len(h.get("workers") or []) for h in hubs)
            workers += len(tree.get("orphan_workers") or [])
            live = sum(1 for h in hubs if h.get("live"))
            running = sum(1 for h in hubs if h.get("sid") in running_sids)
            resumable = sum(1 for h in hubs
                            if h.get("live") and h.get("sid") not in running_sids)
            return {"hubs": len(hubs), "workers": workers, "live_hubs": live,
                    "running": running, "resumable": resumable}
        except Exception:
            pass
    meta = task.get("session_meta") or {}
    hub_sids = [sid for sid, m in meta.items()
                if isinstance(m, dict) and (m.get("role") or "hub") == "hub"]
    live = 0
    resumable = 0
    for sid in hub_sids:
        try:
            p = _find_session_path(sid)
            if p and _session_msgcount(p) >= 1:
                live += 1
                if sid not in running_sids:
                    resumable += 1
        except Exception:
            pass
    running = sum(1 for sid in hub_sids if sid in running_sids)
    # A real delegate worker carries a "session" key (None when a worktree exists
    # but no session ran yet); the placeholder "no worker recorded yet" entry does
    # not, so `"session" in w` counts only genuinely-registered workers.
    workers = sum(1 for w in worker_targets(task) if "session" in w)
    return {"hubs": len(hub_sids), "workers": workers, "live_hubs": live,
            "running": running, "resumable": resumable}


def _board_related(task, tasks=None, rev_map=None):
    """WS4: relation edges for the board card —
        {"from": [{"seq","kind","id"}…],           # edges stored ON this task
         "in":   [{"seq","kind","status","id"}…]}  # edges pointing AT this task

    ONE entry per counterpart across BOTH lists: this is a second, independent
    derivation of the same data the detail line shows, so it routes through
    `canonical_relations` too — a reciprocal pair lands in whichever list wins the
    precedence rather than appearing in both. The `id` is emitted on both sides so
    a consumer can dedup on the task rather than on a machine-local seq; every
    pre-existing key is retained (purely additive).

    `rev_map` (target task id → list of incoming edge dicts) is built ONCE by
    write_board so each card's reverse edges are an O(1) lookup and the board stays
    O(N) — never an N² per-task scan. When `rev_map` is absent (detail path / tests)
    the reverse edges are derived by scanning `tasks` (or `all_tasks()`). Empty lists
    when the task has no relations, so the renderer omits the row."""
    tid = task.get("id")
    if rev_map is not None:
        rev = list(rev_map.get(tid) or [])
    else:
        rev = []
        try:
            scan = tasks if tasks is not None else all_tasks()
        except Exception:
            scan = []
        for other in scan:
            if not isinstance(other, dict) or other.get("id") == tid:
                continue
            for r in (other.get("related") or []):
                if r.get("id") == tid:
                    rev.append({"seq": other.get("seq"), "id": other.get("id"),
                                "kind": r.get("kind"), "status": task_status(other)})
    # rev is always a list (never None), so the resolver reuses the O(1) reverse
    # index / the scan just done and never re-scans the store itself.
    rels = canonical_relations(task, rev=rev)
    out = [{"seq": r["seq"], "kind": r["kind"], "id": r["id"]}
           for r in rels if r["dir"] == "out"]
    inn = [{"seq": r["seq"], "kind": r["kind"], "status": r["status"], "id": r["id"]}
           for r in rels if r["dir"] == "in"]
    return {"from": out, "in": inn}


def _board_view_model(task, live_sids=None, tasks=None, rev_map=None, knowledge=False,
                      live_seqs=None):
    """Flatten a task into the plain dict the HTML board renders — every field the
    card shows, including the derived briefing + the resume one-liner. Decouples
    rendering (tools/render_board.py, stdlib + categories only) from the store.

    `live_sids` (a set of running session ids, supplied by WS5 when available) marks
    per-session live dots; absent it defaults to no live sessions (graceful degrade).

    `knowledge` (SECOND-BRAIN-GATED, default off) adds the `"knowledge"` key — the
    task's cited `[[notes]]` — for the board's per-card "Related knowledge" panel.
    Left None when off, so the renderer omits the panel and a public user sees no
    change."""
    cur = task_status(task)
    glyph = STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED else STATUS_GLYPH.get(cur, "○")
    # The BOARD's 4-state display status folds the live-session signal into the stored
    # status (the old separate live column is gone). closed → closed; a task with a
    # running session → `live` (green, regardless of new/in-progress); an in-progress
    # task with NO running session → `paused` (yellow); an untouched task → `new`.
    # The green state is called `live` so "active" only ever means the stored lifecycle.
    # This is a board-only display overlay — the STORED status (open/active/closed) is
    # unchanged, so the terminal/markdown views and every stored-status contract are
    # untouched.
    _seq0 = task.get("seq")
    _is_live = bool(live_seqs) and _seq0 is not None and _seq0 in live_seqs
    if cur == STATUS_CLOSED:
        status_disp = "closed"
    elif _is_live:
        status_disp = "live"
    elif cur == STATUS_ACTIVE:
        status_disp = "paused"
    else:
        status_disp = "new"
    # 3-tuple (base, dir, abspath) — WS6 links the basename via the editor scheme and
    # copies the abspath; a legacy 2-tuple consumer still unpacks (base, dir, *_).
    files = [(os.path.basename(p), os.path.dirname(p) or ".", p)
             for p in (task.get("files") or [])[-8:]]
    color = task.get("color")
    eff = task.get("effort") or ""
    # The hub/pinned resume one-liner + its last-activity ts, from the SAME
    # computation the terminal task-detail uses (resume_command wraps _resume_target).
    rt = _resume_target(task, None)
    resume_main = None
    if rt:
        resume_main = {
            "command": rt["command"],
            "activity": rel_time(rt["ts"]),
            "pinned": rt["pinned"],
            "fresh": rt["fresh"],
            "label": "Resume (pinned)" if rt["pinned"] else "Resume (hub)",
        }
    seq = task.get("seq")
    # The simple OPEN command — attaches/opens the task in the CURRENT session (the
    # recap), distinct from the resume one-liner that jumps back into the original
    # working session. Only emitted when the task has a seq to address it by.
    open_command = ("/todo %s" % seq) if seq is not None else None
    # WS4 usage view-model (model mix / per-session / phases / prompts) — None/[] when
    # the ledger is off or empty, so the renderer omits the panels.
    bu = _board_usage(task, live_sids)
    hub_cards = _board_hub_cards(task, live_sids)
    # WS7 cost cell / display — never `$n/a`: derived when priced, else the reported $,
    # else a `≥` floor, else `$0.00`. Reuses the ledger already fetched by _board_usage;
    # when there's no ledger, falls back to the delegate-reported task cost.
    if bu and bu.get("usage"):
        stats_cost = _stats_cost(bu["usage"])
    else:
        rep = 0.0
        try:
            rep = float((task.get("cost") or {}).get("total_usd") or 0.0)
        except (TypeError, ValueError):
            rep = 0.0
        stats_cost = _stats_cost({"total_cost_usd": 0.0, "reported_cost_usd": rep,
                                  "any_unpriced": False})
    done, total = step_progress(task)
    steps_cell = ("%d/%d" % (done, total)) if total else None
    try:
        import config as _cfg
        editor_scheme = _cfg.editor_scheme()
    except Exception:
        editor_scheme = "vscode"
    return {
        "seq": seq,
        "title": task.get("title", ""),
        "full_title": task.get("title", ""),       # never truncated in the expanded detail
        "open_command": open_command,              # `/todo <seq>` — open in this session
        "summary": (task.get("summary") or "").strip(),
        "status": cur,
        "status_label": cur,                      # the WORD: open / active / closed
        # BOARD 4-state display status (new / paused / live / closed) — folds the
        # live-session signal into the stored status (see above). Drives the status pill,
        # the row breathing, the category counts, and the status filter — all from ONE
        # value so they can never disagree. `live` is the raw running-session boolean.
        "status_display": status_disp,
        "live": _is_live,
        "glyph": glyph,
        "color": color,
        "tag": cat_tag(color),
        "label": (cats.label(color) if (cats and color) else ""),
        # whether the user has overridden this category's tag/label (board marks it)
        "overridden": bool(cats and color and hasattr(cats, "overridden_keys")
                           and cats.normalize(color) in cats.overridden_keys()),
        "effort": eff,
        "effort_label": EFFORT_WORD.get(eff, ""),
        "effort_gauge": EFFORT_GAUGE.get(eff, EFFORT_GAUGE_EMPTY),
        "activity": rel_time(task.get("updated_ts")),
        "stats": task_stats_line(task),            # compact time/cost line ('' when none)
        "goal": (task.get("goal") or "").strip(),
        "state": (task.get("state") or "").strip(),
        # ACTIVE steps only (a superseded one is off the checklist); the shape is
        # unchanged, so a task with nothing superseded renders exactly as before.
        "steps": [{"text": _steps.text(s), "done": _steps.is_done(s)}
                  for _i, s in _steps.live(task.get("steps"))],
        "progress": list(step_progress(task)),     # [done, total] — active steps only
        "decisions": _dec.live_texts(task.get("decisions")),
        # The append-only HISTORY trail (`update --log` → `history`, entries {ts,text}).
        # Off the normal resume path in the terminal; the board renders it collapsed
        # (secondary to the current snapshot), mirroring `/todo <n> history`.
        "history": [{"ts": e.get("ts", ""), "text": e.get("text", "")}
                    for e in (task.get("history") or [])],
        "repos": list(task.get("projects") or []),
        "prs": merged_prs(task),
        "stories": merged_stories(task),
        "story_refs": _story_refs(task),   # WS13: {id,url} per story, for the board STORY column
        # F5 correspondence: peer pairs (links) + fork provenance. Empty/None on a task
        # with no correspondence, so the renderer emits nothing (parity preserved).
        "links": list(task.get("links") or []),
        "forked_from": task.get("forked_from") or None,
        "files": files,
        "pinned": bool(task.get("pinned_session")),
        "resume": rt["command"] if rt else None,   # back-compat (plain command string)
        "resume_main": resume_main,                # hub/pinned: command + activity + label
        "workers": worker_targets(task),           # de-emphasised worker subsection
        # WS4 usage panels — model mix + totals + derivation, the per-session breakdown,
        # the WS3 work-mix phases, and the prompts preview. `usage` is None / the lists
        # empty when the ledger is off/empty so the renderer omits the blocks.
        "usage": bu["usage"] if bu else None,
        "sessions": bu["sessions"] if bu else [],
        "phases": bu["phases"] if bu else [],
        "prompts_preview": _board_prompts(task),
        "prompts_full": _board_prompts_all(task),   # WS6 full trail (session-attributed)
        # WS7 → WS6 contract keys. `stats_cost` never says n/a; `steps_cell`/`cost_cell`
        # are the promoted grid columns; `hub_sessions` are the per-hub one-liners for
        # the merged Sessions section; `editor_scheme` drives the clickable file links.
        "stats_cost": stats_cost,
        "steps_cell": steps_cell,
        "cost_cell": stats_cost["text"],
        "hub_sessions": _hub_sessions(task, live_sids),
        # board B10–B14: the per-hub cards (each baking in its own prompts, cost +
        # work-mix, and nested worker sessions) + the task-wide stdev cost bands.
        "hubs": hub_cards["hubs"],
        "cost_thresholds": hub_cards["cost_thresholds"],
        "editor_scheme": editor_scheme,
        # WS4: compact session-tree counts + relation edges. Empty (all-zero counts /
        # empty edge lists) on a bare task, so the renderer omits both rows. `session_tree`
        # (NOT `sessions`, which is TAKEN by the per-session usage rows above) holds the
        # hub/worker/live counts; `related` holds outgoing + derived incoming edges.
        "session_tree": _board_session_counts(task, live_sids),
        "related": _board_related(task, tasks=tasks, rev_map=rev_map),
        # WS-D second-brain-gated: the cited [[notes]] for the "Related knowledge"
        # panel. None (not []) when the knowledge tier is off, so the renderer's
        # `if t.get("knowledge")` omits the panel and bare tasks are unchanged.
        "knowledge": (sorted(_task_cited_notes(task)) or None) if knowledge else None,
    }


def _open_argv(path):
    """The macOS `open` argv for `path`, honouring (in priority order) the env var
    TASK_STATION_BROWSER, then config.board_browser(): when one is set, open the board
    in that browser app — `open -a "<App>" <path>`; else `open <path>` (system default).
    Pure / testable — builds the argv list, runs nothing."""
    app = os.environ.get("TASK_STATION_BROWSER") or None
    if not app:
        try:
            import config
            app = config.board_browser()
        except Exception:
            app = None
    if app:
        return ["open", "-a", app, path]
    return ["open", path]


def _open_path(path):
    """Best-effort open of `path` in the configured browser, else the OS default app
    (macOS `open`; see _open_argv). Never raises and returns False off-darwin / on any
    failure — the board is already written, so opening is purely a convenience."""
    if sys.platform != "darwin":
        return False
    try:
        return subprocess.run(_open_argv(path), capture_output=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


_BOARD_VER_RE = re.compile(r'name="ts-board-version" content="([0-9]+\.[0-9]+\.[0-9]+)"')


def _semver_tuple(s):
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$", str(s or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def _semver_gt(a, b):
    """True iff version `a` is STRICTLY newer than `b` (both X.Y.Z). False when either
    is missing/unparseable — we never block a write on absent version info."""
    ta, tb = _semver_tuple(a), _semver_tuple(b)
    return bool(ta and tb and ta > tb)


def _existing_board_version(path):
    """The plugin version stamped into an existing board.html (its
    `<meta name="ts-board-version">`), or None if absent/unreadable. Only the head is
    read — the stamp lives in <head>."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except Exception:
        return None
    m = _BOARD_VER_RE.search(head)
    return m.group(1) if m else None


# ---- F1: server-side foreign (peer/org) rendering --------------------------------------
#
# When Interbrain is ON and peer feed files exist, foreign tasks are parsed from the
# CANONICAL feed serialization (`window.__TSFEED_<alias> = <json>;` — what `feeds._feed_js`
# writes and the two-machine sync will produce) and mapped into the board view-model so
# they render through the SAME _row/section builders as local tasks — read-only (owner chip
# + 🔒 + memo-only, no sessions/prompts/resume). Foreign tasks NEVER touch the store.
#
# `lib/feeds.py` owns the format: parsing (`feeds.parse_feed_file`) and the peers-then-demo
# load order (`feeds.peer_feed_files`) live there, so there is exactly ONE implementation.
# (The demo fixtures under fixtures/demo-feeds/ are canonical too, as of #444 — they used
# to be client-side IIFEs, which this path silently skipped. Any legacy non-canonical file
# is still skipped rather than fatal.)

_OWNER_FALLBACK_COLOR = "#7f8a9c"


def _interbrain_on(data_dir):
    """Resolve config.interbrain_mode() for the board: on/off explicit; `auto` → on when
    brains.json has >1 brain OR any peer feed file exists. Off (and any error) ⇒ False, so
    the DEFAULT board is byte-parity with the pre-federation render."""
    try:
        import config as _cfg
        mode = _cfg.interbrain_mode()
    except Exception:
        return False
    if mode == "on":
        return True
    if mode == "off":
        return False
    n_brains = 1
    try:
        import brains as _brains
        n_brains = len(_brains.load(data_dir).get("brains", {})) or 1
    except Exception:
        n_brains = 1
    try:
        import feeds as _feeds
        return n_brains > 1 or bool(_feeds.peer_feed_files(data_dir))
    except Exception:
        return n_brains > 1


def _knowledge_plane_on(vault=None):
    """Resolve config.knowledge_plane_mode() for the board's two-plane graph: on/off
    explicit; `auto` → on when a vault is configured AND it yields at least one parseable
    note. Off (and any error) ⇒ False.

    WHY AUTO IS SAFE AS A DEFAULT: a user with no vault resolves auto → off, and their
    board is byte-identical to today's — there is no plane, no note node, no cross-plane
    edge. That is the same parity law `_interbrain_on` obeys above, and it is the reason
    this gate can default to `auto` rather than off. The gate is read-only: nothing on
    this path writes into a vault (that is `config.knowledge_graph_enabled()`, a
    different switch entirely).

    An explicit `vault` argument wins over config, the way `obsidian_sync.resolve_vault`
    lets a caller pass one directly."""
    try:
        import config as _cfg
        mode = _cfg.knowledge_plane_mode()
    except Exception:
        return False
    if mode == "on":
        return True
    if mode == "off":
        return False
    try:
        v = vault or _cfg.obsidian_vault()
        return bool(v) and bool(_knowledge.vault_notes(v))
    except Exception:
        return False


def board_notes(vault=None):
    """The knowledge plane's corpus for the board — [] unless the plane resolves ON.

    ONE GLOBAL CORPUS of the whole vault, on every render: not a per-task tree and not a
    filtered slice, because the knowledge layer is a plane in its own right rather than a
    derived view of the task layer. Guarded end-to-end — an unreadable or absent vault
    yields [], never an exception, so a vault on an unmounted drive degrades to today's
    single-plane board. Mirrors `foreign_view_models`: the gate is checked HERE, so the
    caller can hand the result straight to `build_render_graph`."""
    try:
        if not _knowledge_plane_on(vault):
            return []
        import config as _cfg
        return _knowledge.vault_notes(vault or _cfg.obsidian_vault())
    except Exception:
        return []


def _foreign_view_model(feed, ftask):
    """Map ONE foreign feed task → the board view-model dict, flagged `foreign=True` and
    read-only. Owner/handle/brain/colour come from the feed; the category/effort/status
    fields reuse the LOCAL task vocabulary so the shared _row builder renders foreign and
    local rows identically. Pure; never touches the store."""
    owner = ftask.get("owner") or feed.get("owner") or feed.get("alias") or "peer"
    uuid8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (owner, uuid8 or "?"))
    # foreign tasks carry their feed's brain field if present, else the alias itself.
    brain = ftask.get("brain") or feed.get("alias") or owner
    owner_color = feed.get("color") or _OWNER_FALLBACK_COLOR
    owner_color_dark = feed.get("color_dark") or owner_color
    # canonical feeds carry category as {key,tag,dot,hex,hex_dark}; tolerate a bare key.
    cat = ftask.get("category")
    if isinstance(cat, dict):
        color = cat.get("key") or ""
        tag = cat.get("tag") or ""
    else:
        color = cat or ""
        tag = cat_tag(color) if color else ""
    st = (ftask.get("status") or "open").lower()
    if st == "closed":
        status, sdisp = "closed", "closed"
    elif st == "active":
        status, sdisp = "active", "paused"   # foreign never shows `live` (no local session)
    else:
        status, sdisp = "open", "new"
    eff = ftask.get("effort") or ""
    effU = eff.upper()
    digest = ftask.get("digest") or {}
    signals = ftask.get("signals") or {}
    # raw normalized signal ids (feed form) — for cross-brain graph edge matching.
    sig_prs = [p for p in (signals.get("prs") or []) if p]
    sig_stories = [s for s in (signals.get("stories") or []) if s]
    return {
        "foreign": True,
        "editable": False,
        "owner": owner,
        "owner_color": owner_color,
        "owner_color_dark": owner_color_dark,
        "handle": handle,
        "brain": brain,
        "seq": None,
        "id": "foreign:%s:%s" % (owner, uuid8 or handle),
        "uuid8": uuid8,
        "title": ftask.get("title") or "",
        "full_title": ftask.get("title") or "",
        "status": status,
        "status_label": status,
        "status_display": sdisp,
        "live": False,
        "color": color,
        "tag": tag,
        "label": "",
        "overridden": False,
        "effort": eff,
        "effort_label": EFFORT_WORD.get(effU, ""),
        "effort_gauge": EFFORT_GAUGE.get(effU, EFFORT_GAUGE_EMPTY),
        "activity": rel_time(ftask.get("updated_ts")),
        "summary": "",
        "goal": (digest.get("goal") or "").strip(),
        "state": (digest.get("state") or "").strip(),
        "steps": [],
        "progress": [int(digest.get("steps_done") or 0),
                     int(digest.get("steps_total") or 0)],
        "decisions": [_dec.text(d) for d in (digest.get("decisions_tail") or [])],
        "history": [],
        "prs": [{"label": "PR %s" % parse_pr_number(p), "num": parse_pr_number(p),
                 "url": None} for p in sig_prs],
        "stories": [{"id": s} for s in sig_stories],
        "story_refs": [{"id": s, "url": None} for s in sig_stories],
        "repos": [],
        "files": [],
        # A peer that exports with trail_visibility=full carries `prompts` in its feed
        # entry (its own sync_safe stripper let them through). Pass them to the detail view
        # so "Peer detail renders whatever visibility grants" (F5.4); empty otherwise.
        "prompts_preview": list((ftask.get("prompts") or []))[:5],
        "prompts_full": list(ftask.get("prompts") or []),
        "usage": None,
        "sessions": [],
        "phases": [],
        "hubs": [],
        "session_tree": {},
        "related": {},
        "knowledge": None,
        "resume": None,
        "resume_main": None,
        "open_command": None,
        "shared_org": bool(ftask.get("shared_org")),
        "shares": list(ftask.get("shares") or []),
        # raw signal ids kept for the cross-brain graph edges (not rendered in the row).
        "signal_prs": sig_prs,
        "signal_stories": sig_stories,
    }


def foreign_view_models(data_dir=None):
    """All foreign (peer/org) view-models for the board — [] unless Interbrain
    resolves ON. Reads every canonical peer feed under feeds/{peers,demo}/, maps each
    task via _foreign_view_model, and returns them in feed-file order. Guarded end-to-end:
    a bad feed is skipped, never fatal. `data_dir` defaults to paths.data_dir()."""
    dd = data_dir or paths.data_dir()
    if not _interbrain_on(dd):
        return []
    import feeds as _feeds
    out = []
    for path in _feeds.peer_feed_files(dd):
        feed = _feeds.parse_feed_file(path)
        if not feed:
            continue
        # never treat the local brain's own feed as foreign.
        if (feed.get("kind") or "") in ("self", "archive"):
            continue
        for ftask in feed.get("tasks") or []:
            try:
                out.append(_foreign_view_model(feed, ftask))
            except Exception:
                continue
    return out


# ============================ F5 — correspondence ============================
# Collaboration WITHOUT shared writes: link (record a peer pair), fork (copy a peer node's
# digest into my own task + provenance), subscribe (mint memos when the peer feed advances),
# and per-node trail_visibility (what my feed exports). All read CANONICAL peer feeds
# (feeds/{peers,demo}/*.js — the `window.__TSFEED_<alias> = {json};` form real sync
# produces + tests seed + the demo fixtures use as of #444). Correspondence targets that
# canonical form, so it is sync-ready; any legacy non-canonical file is skipped, not fatal.

def _all_peer_feeds(data_dir=None):
    """Every CANONICAL peer/org feed as a parsed dict (self/archive excluded), feed-file
    order. A non-canonical file parses to None and is skipped. Never raises."""
    import feeds as _feeds
    dd = data_dir or paths.data_dir()
    out = []
    for path in _feeds.peer_feed_files(dd):
        feed = _feeds.parse_feed_file(path)
        if not feed or (feed.get("kind") or "") in ("self", "archive"):
            continue
        out.append(feed)
    return out


def _feed_rev(feed):
    """A peer feed's revision — its own `rev` if present, else a stable content hash over
    its tasks (so subscriptions can diff even feeds that don't stamp one)."""
    if not feed:
        return ""
    r = feed.get("rev")
    if r:
        return str(r)
    try:
        basis = json.dumps(feed.get("tasks") or [], sort_keys=True, default=str)
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _resolve_peer_ref(ref, data_dir=None):
    """Resolve a peer task ref → (feed, ftask) or (None, None). Accepts the handle
    (`jpark-201`), the `<alias>-<uuid8>` form, a bare uuid8, or `<alias>-<uuid8-prefix>`.
    Scans every canonical peer feed; first match wins."""
    ref = (ref or "").strip()
    if not ref:
        return None, None
    for feed in _all_peer_feeds(data_dir):
        alias = feed.get("alias") or feed.get("owner") or ""
        for ftask in feed.get("tasks") or []:
            handle = ftask.get("handle") or ""
            u8 = (ftask.get("uuid8") or "")[:8]
            if ref == handle or (u8 and ref == u8) or (alias and ref == "%s-%s" % (alias, u8)):
                return feed, ftask
            if alias and ref.startswith(alias + "-"):
                suf = ref[len(alias) + 1:]
                if suf and (suf == u8 or (u8 and u8.startswith(suf))
                            or handle.endswith("-" + suf)):
                    return feed, ftask
    return None, None


def add_link(task, alias, uuid8, handle=None, kind="link"):
    """Record a correspondence link to a peer task on `task["links"]` (created on first
    link). Idempotent — a duplicate (alias, uuid8) is a no-op returning False. Does NOT
    save. `kind` distinguishes a manual link from a fork's auto-link."""
    alias = (alias or "").strip()
    uuid8 = (uuid8 or "")[:8]
    if not alias or not uuid8:
        return False
    links = task.setdefault("links", [])
    for l in links:
        if l.get("alias") == alias and (l.get("uuid8") or "")[:8] == uuid8:
            return False
    links.append({"alias": alias, "uuid8": uuid8,
                  "handle": handle or ("%s-%s" % (alias, uuid8)),
                  "kind": kind, "ts": _now()})
    return True


def cmd_link(a):
    """`task-station link --task <ref> --peer <alias>-<n|uuid8>` — record a correspondence
    pair between my task and a peer feed task. One-way storage (peer feeds are read-only);
    the pair renders on the detail, the board row, and as a dashed graph edge."""
    task = resolve_ref(getattr(a, "task", None))
    if not task:
        print("link: no task matching %r" % getattr(a, "task", None))
        return
    feed, ftask = _resolve_peer_ref(getattr(a, "peer", None))
    if not ftask:
        print("link: no peer task matching %r (looked in canonical feeds/{peers,demo}/*.js)"
              % getattr(a, "peer", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    label = task.get("seq", task["id"][:8])
    if add_link(task, alias, u8, handle):
        add_event(task, "link", "linked ↔ %s: %s" % (handle, ftask.get("title") or ""),
                  getattr(a, "session", None))
        task["updated_ts"] = _now()
        save_task(task)
        _obsidian_sync(task)
        print("linked #%s ↔ %s (%s)" % (label, handle, ftask.get("title") or ""))
    else:
        print("link: #%s is already linked to %s — no change" % (label, handle))


def cmd_fork(a):
    """`task-station fork --from <alias>-<n|uuid8> [--title ...]` — create MY task from a
    peer feed node: copy its digest (goal/state/decisions + any summary/glossary/steps the
    feed carries), record `forked_from` provenance (alias, uuid8, at_rev), auto-link the
    pair, and auto-attach to a brain (F4). The peer feed is never mutated."""
    feed, ftask = _resolve_peer_ref(getattr(a, "from_ref", None))
    if not ftask:
        print("fork: no peer task matching %r (looked in canonical feeds/{peers,demo}/*.js)"
              % getattr(a, "from_ref", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    digest = ftask.get("digest") or {}
    title = (getattr(a, "title", None) or ftask.get("title") or "Forked task").strip()
    summary = (ftask.get("summary") or digest.get("goal") or "").strip()
    # map the peer category key to a local category (unknown → default/none).
    cat = ftask.get("category")
    color = cat.get("key") if isinstance(cat, dict) else (cat or None)
    task = new_task(title, summary, color=color, effort=ftask.get("effort"))
    # DIGEST DOWNLOAD — copy every digest field the feed carries.
    if digest.get("goal"):
        task["goal"] = digest["goal"].strip()
    if digest.get("state"):
        task["state"] = digest["state"].strip()
    # The feed already stripped superseded decisions; coerce to plain text so a peer
    # writing the rich shape can never land a dict in my own decisions log.
    dec = [t for t in (_dec.text(d) for d in (digest.get("decisions_tail") or [])) if t]
    if dec:
        task["decisions"] = list(dec)
    if isinstance(ftask.get("steps"), list) and ftask["steps"]:
        # ACTIVE steps only, coerced to the plain shape: a step the peer retired is not
        # work I am inheriting, and my checklist starts from what is still to do.
        task["steps"] = [{"text": _steps.text(s), "done": _steps.is_done(s)}
                         for _i, s in _steps.live(ftask["steps"])]
    if isinstance(ftask.get("glossary"), list) and ftask["glossary"]:
        task["glossary"] = list(ftask["glossary"])
    # PROVENANCE — where this fork came from + the peer feed rev at fork time.
    task["forked_from"] = {"alias": alias, "uuid8": u8, "handle": handle,
                           "title": ftask.get("title") or "", "at_rev": _feed_rev(feed),
                           "ts": _now()}
    add_link(task, alias, u8, handle, kind="fork")
    create_with_seq(task)              # mint seq + persist (forked_from/links ride along)
    brain = auto_attach_brain(task, getattr(a, "session", None))
    add_event(task, "fork", "forked from %s (%s) @rev %s"
              % (handle, ftask.get("title") or "", task["forked_from"]["at_rev"]),
              getattr(a, "session", None))
    touch(task, note="forked from %s" % handle)
    save_task(task)
    _obsidian_sync(task)
    print("📋 Forked %s → task #%s [%s] %s (brain: %s). /todo %s -s starts a session."
          % (handle, task.get("seq"), task["id"][:8], task["title"], brain, task.get("seq")))
    for line in cat_lines(task.get("color")):
        print(line)


# -- F5.3 subscriptions: mint memos when a subscribed peer feed advances --------

_SUBS_CHECK_INTERVAL = 120     # seconds between throttled (hook-path) checks


def _feed_task(feed, uuid8):
    """The task dict in `feed` whose uuid8 matches (prefix-tolerant), or None."""
    u8 = (uuid8 or "")[:8]
    for ftask in (feed or {}).get("tasks") or []:
        fu = (ftask.get("uuid8") or "")[:8]
        if fu and (fu == u8 or fu.startswith(u8) or u8.startswith(fu)):
            return ftask
    return None


def _subscription_memo_text(link, feed, ftask):
    """Build the capped memo body for a subscribed peer feed that advanced. Includes only
    the watched kinds (checkpoint/decision/trail). Format: `<handle> new <kind>: …`."""
    handle = link.get("handle") or link.get("alias") or "peer"
    on = (link.get("subscribe") or {}).get("on") or []
    if not ftask:
        return "%s: feed advanced (rev %s)" % (handle, _feed_rev(feed))
    digest = ftask.get("digest") or {}
    parts = []
    if "checkpoint" in on and (digest.get("state") or "").strip():
        parts.append("checkpoint: %s" % digest["state"].strip())
    if "decision" in on and (digest.get("decisions_tail") or []):
        parts.append("decision: %s" % _dec.text(digest["decisions_tail"][-1]).strip())
    if "trail" in on and (ftask.get("prompts") or []):
        p = ftask["prompts"][-1]
        txt = p if isinstance(p, str) else (p.get("text") or "")
        if txt:
            parts.append("trail: %s" % txt.strip())
    if not parts:
        parts.append("update: %s" % (digest.get("state") or ftask.get("title") or "advanced"))
    return ("%s new %s" % (handle, "; ".join(parts)))[:MEMO_LINE_MAX]


def _subscriptions_check(session=None):
    """Diff every subscribed link's peer feed rev vs its last-seen rev; when it advanced,
    mint ONE memo onto my task (idempotent per rev — last_rev is bumped immediately) and
    persist. Returns the number of memos minted. Fail-open: a bad feed is skipped."""
    feeds = {}
    try:
        for feed in _all_peer_feeds():
            feeds[feed.get("alias") or feed.get("owner")] = feed
    except Exception:
        return 0
    minted = 0
    for t in sorted_tasks():
        changed = False
        for l in (t.get("links") or []):
            s = l.get("subscribe")
            if not s:
                continue
            feed = feeds.get(l.get("alias"))
            if not feed:
                continue
            rev = _feed_rev(feed)
            if not rev or rev == s.get("last_rev"):
                continue                       # unchanged → idempotent no-op
            ftask = _feed_task(feed, l.get("uuid8"))
            text = _subscription_memo_text(l, feed, ftask)
            if text:
                memo_send(t, text, from_sid=None)
                minted += 1
            s["last_rev"] = rev
            changed = True
        if changed:
            t["updated_ts"] = _now()
            save_task(t)
            _obsidian_sync(t)
    return minted


def _subs_throttled():
    """True when a throttled check ran within _SUBS_CHECK_INTERVAL (so the on_stop hook
    path stays cheap). Stamps the run time on a False. Fail-open (never blocks)."""
    p = os.path.join(paths.data_dir(), "subscriptions.last")
    now = _now()
    try:
        with open(p, encoding="utf-8") as f:
            last = float((f.read() or "0").strip() or 0)
    except Exception:
        last = 0
    if now - last < _SUBS_CHECK_INTERVAL:
        return True
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass
    return False


def cmd_subscribe(a):
    """`task-station subscribe --task <ref> --peer <alias>-<ref> --on checkpoint,decision,
    trail` — watch a peer feed task; a later `subscriptions check` mints a memo when it
    advances. Ensures the link exists, then stores the subscription ON that link with a
    baseline rev (so only FUTURE changes mint)."""
    task = resolve_ref(getattr(a, "task", None))
    if not task:
        print("subscribe: no task matching %r" % getattr(a, "task", None))
        return
    feed, ftask = _resolve_peer_ref(getattr(a, "peer", None))
    if not ftask:
        print("subscribe: no peer task matching %r" % getattr(a, "peer", None))
        return
    alias = feed.get("alias") or feed.get("owner")
    u8 = (ftask.get("uuid8") or "")[:8]
    handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
    on = [k.strip() for k in (getattr(a, "on", "") or "").split(",") if k.strip()]
    add_link(task, alias, u8, handle)          # ensure a link to hang the subscription on
    for l in task.get("links") or []:
        if l.get("alias") == alias and (l.get("uuid8") or "")[:8] == u8:
            l["subscribe"] = {"on": on, "last_rev": _feed_rev(feed)}
            break
    task["updated_ts"] = _now()
    save_task(task)
    _obsidian_sync(task)
    print("subscribed #%s → %s on %s (baseline rev %s)"
          % (task.get("seq", task["id"][:8]), handle, ",".join(on) or "—", _feed_rev(feed)))


def cmd_subscriptions(a):
    """`task-station subscriptions <check|list>`. `check` diffs subscribed peer feeds and
    mints memos (the on_stop-hook path passes --throttle so it self-throttles + stays
    silent); `list` prints every active subscription. Fail-open."""
    sub = getattr(a, "sub", None) or "check"
    if sub == "list":
        any_ = False
        for t in sorted_tasks():
            for l in (t.get("links") or []):
                s = l.get("subscribe")
                if s:
                    any_ = True
                    print("#%s → %s on %s (last_rev %s)"
                          % (t.get("seq", t["id"][:8]), l.get("handle"),
                             ",".join(s.get("on") or []) or "—", s.get("last_rev")))
        if not any_:
            print("(no subscriptions)")
        return
    if getattr(a, "throttle", False) and _subs_throttled():
        return                                 # cheap silent no-op on the hook path
    minted = _subscriptions_check(getattr(a, "session", None))
    if not getattr(a, "throttle", False):
        print("subscriptions check: minted %d memo(s)" % minted)


# ================= F6 — artifact capture + cross-person auto-link =============

def _autolink_task_signals(task, session=None):
    """F6.2 cross-person auto-link: for every PR/story signal on `task`, scan mounted peer
    feeds for a task carrying the SAME signal id and auto-create the correspondence link +
    mint a memo. Idempotent — add_link dedups, so an already-linked pair mints nothing.
    Signal ids are the SAME normalized forms the feeds carry (feeds._pr_signal_id /
    story_ref), so a captured PR pairs with the peer reviewing it. Returns links created."""
    try:
        import feeds as _feeds
    except Exception:
        return 0
    my_sigs = {}                                   # canonical id → "PR"|"story"
    for p in merged_prs(task):
        sid = _feeds._pr_signal_id(p.get("url"))
        if sid:
            my_sigs.setdefault(sid, "PR")
    for s in _story_refs(task):
        if s.get("id"):
            my_sigs.setdefault(s["id"], "story")
    if not my_sigs:
        return 0
    made = 0
    my = "%s-%s" % (_owner() or "me", task.get("seq", task["id"][:8]))
    for feed in _all_peer_feeds():
        alias = feed.get("alias") or feed.get("owner")
        for ftask in feed.get("tasks") or []:
            fsig = ftask.get("signals") or {}
            fids = set(fsig.get("prs") or []) | set(fsig.get("stories") or [])
            shared = [s for s in my_sigs if s in fids]
            if not shared:
                continue
            u8 = (ftask.get("uuid8") or "")[:8]
            handle = ftask.get("handle") or ("%s-%s" % (alias, u8))
            if add_link(task, alias, u8, handle, kind="signal"):
                sig = shared[0]
                # Two-way wording: my side links to the peer over the shared signal (the
                # peer independently captures + links back on sync — the reciprocal side).
                memo_send(task, "%s linked: shared %s %s ↔ %s"
                          % (my, my_sigs[sig], sig, handle), from_sid=None)
                add_event(task, "autolink",
                          "auto-linked ↔ %s (shared %s %s)" % (handle, my_sigs[sig], sig),
                          session)
                made += 1
    return made


def cmd_capture_artifacts(a):
    """PostToolUse capture: scan a tool RESULT (stdin, or --text) for PR/work-item URLs and
    append them (deduped) to the attached task's prs[]/stories[], with ONE capped event,
    then run the cross-person auto-link. Suppressed in delegate workers (env guard); a
    no-op when the session isn't attached / nothing matches. Fail-open — never raises."""
    try:
        if os.environ.get("TASK_STATION_SUPPRESS"):
            return
        session = getattr(a, "session", None)
        if not session:
            return
        tid = get_link(session)
        if not tid or tid == SKIP_SENTINEL:
            return
        task = load_task(tid)
        if not task:
            return
        data = getattr(a, "text", None)
        if not data:
            try:
                data = sys.stdin.read()
            except Exception:
                data = ""
        import artifacts
        hits = artifacts.scan(data)
        if not hits:
            return
        changed = []
        for h in hits:
            if h["kind"] == "pr":
                if add_pr(task, h["url"]):
                    changed.append(h)
            else:
                if add_story(task, h["url"]):
                    changed.append(h)
        if changed:
            labels = ", ".join((h.get("id") or h["url"]) for h in changed[:5])
            more = "" if len(changed) <= 5 else " (+%d)" % (len(changed) - 5)
            add_event(task, "artifact",
                      "captured %d artifact(s): %s%s" % (len(changed), labels, more), session)
            task["updated_ts"] = _now()
        # Auto-link fires on ANY signal now on the task (idempotent), so a re-capture of an
        # already-stored PR still forms a newly-possible cross-person pair.
        _autolink_task_signals(task, session)
        save_task(task)
        _obsidian_sync(task)
    except Exception:
        return                                     # capture is best-effort; never disrupts


def _augment_graph_foreign(graph, raw, foreign_vms, owner_alias, brain_by_seq):
    """Return a NEW render-graph dict with owner/brain stamped on every LOCAL task node
    (owner=`owner_alias`) plus FOREIGN task nodes and dashed cross-brain shared-signal
    edges (kind `xbrain`) for peer tasks that share a PR/story id with a local task. The
    local endpoint node is created if the base graph didn't already carry it (a task that
    shares a signal ONLY across brains). Input is not mutated; foreign nodes appear only
    when they connect (mirrors the graph's connected-only rule). Pure; guarded by caller."""
    import copy
    g = {"nodes": [copy.deepcopy(n) for n in (graph or {}).get("nodes") or []],
         "edges": [copy.deepcopy(e) for e in (graph or {}).get("edges") or []],
         "singletons": dict((graph or {}).get("singletons") or {})}
    by_id = {n["id"]: n for n in g["nodes"]}
    # stamp owner/brain on the existing local task nodes.
    for n in g["nodes"]:
        if n.get("type") == "task":
            n["owner"] = owner_alias
            n["brain"] = brain_by_seq.get(n.get("seq"), "main")
    # local signal index — feed-normalized ids (feeds._pr_signal_id) so they match the
    # foreign feeds' signal ids; stories keyed by their story_ref id.
    import feeds as _feeds
    local_prs, local_stories, local_meta = {}, {}, {}
    for t in raw:
        seq = t.get("seq")
        if seq is None:
            continue
        prs = set(_feeds._pr_signal_id(p.get("url")) for p in merged_prs(t))
        prs.discard("")
        stories = set(s.get("id") for s in _story_refs(t) if s.get("id"))
        local_prs[seq] = prs
        local_stories[seq] = stories
        cur = task_status(t)
        local_meta[seq] = {
            "title": t.get("title", ""), "color": t.get("color"), "status": cur,
            "glyph": STATUS_GLYPH_CLOSED if cur == STATUS_CLOSED
            else STATUS_GLYPH.get(cur, "○")}

    def _ensure_local(seq):
        nid = "t:%d" % seq
        if nid in by_id:
            return nid
        m = local_meta.get(seq) or {}
        node = {"id": nid, "type": "task", "seq": seq, "title": m.get("title", ""),
                "color": m.get("color"), "status": m.get("status"),
                "glyph": m.get("glyph"), "deg": 0,
                "owner": owner_alias, "brain": brain_by_seq.get(seq, "main")}
        g["nodes"].append(node)
        by_id[nid] = node
        return nid

    for fvm in foreign_vms:
        f_prs = set(fvm.get("signal_prs") or [])
        f_stories = set(fvm.get("signal_stories") or [])
        if not f_prs and not f_stories:
            continue
        matches = []                                    # (local_seq, [shared labels])
        for seq in local_meta:
            shared = ["PR %s" % parse_pr_number(pid)
                      for pid in sorted(f_prs & local_prs.get(seq, set()))]
            shared += ["story %s" % sid
                       for sid in sorted(f_stories & local_stories.get(seq, set()))]
            if shared:
                matches.append((seq, shared))
        if not matches:
            continue
        fid = "f:%s:%s" % (fvm.get("owner"), fvm.get("uuid8") or fvm.get("handle"))
        if fid not in by_id:
            fnode = {"id": fid, "type": "task", "seq": None, "foreign": True,
                     "title": fvm.get("title", ""), "color": fvm.get("color"),
                     "status": fvm.get("status"), "glyph": "◇",
                     "owner": fvm.get("owner"), "brain": fvm.get("brain"),
                     "owner_color": fvm.get("owner_color"),
                     "handle": fvm.get("handle"), "label": fvm.get("handle"), "deg": 0}
            g["nodes"].append(fnode)
            by_id[fid] = fnode
        for seq, labels in matches:
            lid = _ensure_local(seq)
            g["edges"].append({"a": fid, "b": lid, "kind": "xbrain", "dir": "none",
                               "weight": 1, "via": labels})

    # F5: explicit correspondence links (`task-station link` / fork auto-link) draw a
    # dashed cross-brain PAIR edge between the local task and the peer it points at — even
    # with NO shared signal. The foreign endpoint node is created on demand. Dedup: never
    # add a second edge between an already-connected pair (shared-signal edge wins).
    fvm_by_key = {}
    for fvm in foreign_vms:
        fvm_by_key[(fvm.get("owner"), (fvm.get("uuid8") or "")[:8])] = fvm

    def _edge_exists(x, y):
        return any((e["a"] == x and e["b"] == y) or (e["a"] == y and e["b"] == x)
                   for e in g["edges"])

    for t in raw:
        seq = t.get("seq")
        if seq is None:
            continue
        for lk in (t.get("links") or []):
            fvm = fvm_by_key.get((lk.get("alias"), (lk.get("uuid8") or "")[:8]))
            if not fvm:
                continue
            fid = "f:%s:%s" % (fvm.get("owner"), fvm.get("uuid8") or fvm.get("handle"))
            if fid not in by_id:
                fnode = {"id": fid, "type": "task", "seq": None, "foreign": True,
                         "title": fvm.get("title", ""), "color": fvm.get("color"),
                         "status": fvm.get("status"), "glyph": "◇",
                         "owner": fvm.get("owner"), "brain": fvm.get("brain"),
                         "owner_color": fvm.get("owner_color"),
                         "handle": fvm.get("handle"), "label": fvm.get("handle"), "deg": 0}
                g["nodes"].append(fnode)
                by_id[fid] = fnode
            lid = _ensure_local(seq)
            if not _edge_exists(fid, lid):
                g["edges"].append({"a": fid, "b": lid, "kind": "xbrain", "dir": "none",
                                   "weight": 1, "via": ["linked ↔ %s" % (fvm.get("handle") or "")]})
    # recompute degree over the augmented edge set.
    deg = {}
    for e in g["edges"]:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    for n in g["nodes"]:
        n["deg"] = deg.get(n["id"], 0)
    return g


def write_board(guard_downgrade=False):
    """Render the self-contained HTML board of every task (open + closed) to
    <data_dir>/board.html and return its path. THE board — there is one renderer
    (`tools/render_board.py`), no engine choice. Inline CSS, no server, no external
    assets, no LLM. Shared by `board` and `/todo board`.

    Also exports this machine's feed to <data_dir>/feeds/self.js via `lib/feeds.py` (see
    the export call below), so the feed root is always current for the demo seeder and
    the future sync transport. Federation (peer/org feeds → read-only foreign rows) is
    part of THIS render, gated by `config --interbrain`.

    `guard_downgrade` (the passive/auto path — maybe_refresh_board): refuse to overwrite
    an existing board that a NEWER plugin version rendered, so a stale older-version
    session's turn-end refresh can't clobber a current render back to old markup. The
    explicit `board` command leaves it False (an intentional render always writes)."""
    ensure_seqs()
    raw = sorted_tasks()
    # WS4: build the reverse relation-edge index ONCE (O(total edges)) so each card's
    # incoming ("spawned #N" / "related ←") edges are an O(1) lookup rather than an
    # N² per-task scan. Maps a target task id → [{seq, id, kind, status}] for every
    # task whose `related` list points at it. The SOURCE task's `id` rides along so
    # `canonical_relations` can dedup a reciprocal pair on the task itself rather
    # than on its machine-local seq.
    rev_map = {}
    for t in raw:
        st = task_status(t)
        for r in (t.get("related") or []):
            tgt = r.get("id")
            if tgt:
                rev_map.setdefault(tgt, []).append(
                    {"seq": t.get("seq"), "id": t.get("id"),
                     "kind": r.get("kind"), "status": st})
    # WS-D: the knowledge (co-citation) tier is SECOND-BRAIN-GATED — active only when
    # the opt-in flag is on AND a consumer (an Obsidian vault) is configured. Off by
    # default, so the graph below carries only the UNIVERSAL edges (lineage + semantic).
    knowledge_on = False
    try:
        import config as _cfg_kg
        knowledge_on = bool(_cfg_kg.knowledge_graph_enabled()) and bool(_cfg_kg.obsidian_vault())
    except Exception:
        knowledge_on = False
    # WS5: real process liveness (running Claude pids). Fetched HERE — before the
    # view-models — so each hub/worker card gets its true 3-tier state (running vs merely
    # resumable), not just the transcript-derived flag that survives a crash. Reused for
    # the top live-strip below. Guarded: a sessions-dir hiccup must never block the board.
    try:
        import live_sessions
        live = live_sessions.running()
    except Exception:
        live = []
    live_sids = {r["session_id"] for r in live if r.get("session_id")}
    # The set of task seqs that have a RUNNING session right now (the same signal the
    # breathing dot used). A task with a live session DISPLAYS as `active` (green); an
    # in-progress task with none displays as `paused` (yellow). Derived once here.
    live_seqs = {r.get("task_seq") for r in live if r.get("task_seq") is not None}
    vms = [_board_view_model(t, live_sids=live_sids, rev_map=rev_map,
                             knowledge=knowledge_on, live_seqs=live_seqs)
           for t in raw]
    # F1/F2: Interbrain gate. When OFF (the default) nothing below runs — no foreign VMs,
    # no owner/handle/brain stamping, no focus strip — so the render is byte-parity with the
    # pre-F1 board (the parity law; the one enumerated exception is the help-panel hint,
    # BOARD-BEHAVIOR.md B14). When ON, self VMs gain their display-only handle + owner +
    # brain, and peer/org feeds are rendered server-side as foreign rows.
    interbrain_on = False
    try:
        interbrain_on = _interbrain_on(paths.data_dir())
    except Exception:
        interbrain_on = False
    owner_alias = "me"
    brain_by_seq = {}
    foreign_vms = []
    org_lbl = "Org brain"
    if interbrain_on:
        try:
            import config as _cfg_own
            owner_alias = _cfg_own.owner() or "me"
            org_lbl = _cfg_own.org_label()
        except Exception:
            owner_alias = "me"
        _bmod = _bcfg = None
        try:
            import brains as _bmod
            _bcfg = _bmod.load(paths.data_dir())
        except Exception:
            _bmod = _bcfg = None
        for _t, _vm in zip(raw, vms):
            _seq = _vm.get("seq")
            _vm["_ib"] = True
            _vm["foreign"] = False
            _vm["owner"] = owner_alias
            _vm["handle"] = ("%s-%s" % (owner_alias, _seq)) if _seq is not None else None
            _brain = "main"
            if _bmod is not None and _bcfg is not None:
                try:
                    _brain = _bmod.brain_for(_bcfg, _t.get("id") or "")
                except Exception:
                    _brain = "main"
            _vm["brain"] = _brain
            if _seq is not None:
                brain_by_seq[_seq] = _brain
        try:
            foreign_vms = foreign_view_models(paths.data_dir())
        except Exception:
            foreign_vms = []
    vms = vms + foreign_vms
    # WS-D: the board-level relation graph for the mini-graph panel. build_render_graph
    # augments build_board_graph (lineage, plus gated co-citation) with category +
    # signal HUB nodes and string ids for the clustered SVG. Empty
    # {nodes:[], edges:[]} on a relation-free store, so the renderer omits the panel.
    # …and the KNOWLEDGE PLANE's corpus, resolved once per render. `board_notes` checks
    # the (separate) knowledge-plane gate itself and returns [] when it is off, so this
    # needs no second gate and a user with no vault gets the byte-identical graph they
    # get today. It never raises: an unreadable or unmounted vault degrades to [].
    try:
        notes = board_notes()
    except Exception:
        notes = []
    try:
        graph = build_render_graph(raw, knowledge=knowledge_on, notes=notes)
    except Exception:
        graph = {"nodes": [], "edges": []}
    # F1: fold foreign nodes + dashed cross-brain shared-signal edges into the graph, and
    # stamp owner/brain on every task node (so the focus filter works for self brains too,
    # not only when peers exist). Only when Interbrain is ON — off leaves the graph
    # byte-identical to pre-F1.
    if interbrain_on:
        try:
            graph = _augment_graph_foreign(graph, raw, foreign_vms,
                                           owner_alias, brain_by_seq)
        except Exception:
            pass
    # A STABLE revision hash over the RAW task records (NOT the view-models, whose
    # relative-time strings drift): it changes iff a task is created/modified — touch()
    # bumps a task's stored timestamp on any mutation — and NOT merely from time
    # passing. The board loads a sibling board.rev.js <script> and reloads only when
    # this changes (file:// browsers block local fetch but DO load local scripts).
    rev = hashlib.sha1(
        json.dumps(raw, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    # realpath derefs the ~/.claude/task-station-engine symlink to the real lib/
    # so tools/ (a sibling of lib/) resolves; BASE keeps the stable symlink path
    # for the resume one-liners, so we must NOT use it here.
    real_lib = os.path.dirname(os.path.realpath(__file__))
    tools = os.path.join(os.path.dirname(real_lib), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import render_board
    theme = variant = variant_label = None
    config_rows = []
    board_autorefresh = False
    bare = False
    try:
        import config
        theme = config.active_theme()
        config_rows = config.board_rows()
        board_autorefresh = config.board_autorefresh_enabled()
        bare = config.bare_commands()
    except Exception:
        pass
    if cats and hasattr(cats, "resolve_variant"):
        try:
            variant = cats.resolve_variant()
        except Exception:
            variant = None
    if variant:
        try:
            variant_label = config._variant_label(variant, theme)
        except Exception:
            variant_label = None
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    # B-TS: also pass the absolute epoch so the board rewrites these to the VIEWER's LOCAL
    # time client-side (the formatted strings above are just the no-JS fallback).
    generated_ts = _now()
    # The footer shows the task-station VERSION + when this installed version was
    # written. The plugin root is CLAUDE_PLUGIN_ROOT when set, else real_lib's parent
    # (real_lib is <ver>/lib, so its parent is <ver>); plugin.json lives under
    # .claude-plugin/. version = its "version" string; updated = its mtime (the install
    # time of this version). Both guarded → "" so the board still renders if absent.
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(real_lib)
    pj = os.path.join(plugin_root, ".claude-plugin", "plugin.json")
    version = ""
    repo_url = ""
    try:
        with open(pj, encoding="utf-8") as f:
            pj_data = json.load(f)
        version = pj_data.get("version") or ""
        # The footer links to the repo: prefer "homepage", fall back to "repository".
        repo_url = pj_data.get("homepage") or pj_data.get("repository") or ""
    except Exception:
        version = ""
        repo_url = ""
    updated_ts = None
    try:
        updated_ts = os.path.getmtime(pj)
        updated = datetime.fromtimestamp(updated_ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        updated = ""
        updated_ts = None
    # WS5: the live-session strip (running Claude processes) reuses `live` fetched
    # above (before the view-models), so the strip and the per-card 3-tier state agree.
    html_doc = render_board.render_html(
        vms, theme=theme, variant=variant, variant_label=variant_label,
        generated=generated, commands=_COMMANDS_HELP, config_rows=config_rows,
        board_autorefresh=board_autorefresh, bare=bare, rev=rev,
        version=version, updated=updated, repo_url=repo_url, live_sessions=live,
        graph=graph, generated_ts=generated_ts, updated_ts=updated_ts,
        interbrain=interbrain_on, org_label=org_lbl)
    out = os.path.join(paths.data_dir(), "board.html")
    # Refuse to DOWNGRADE (passive path only): if the on-disk board was stamped by a
    # newer plugin version than the one rendering now, leave it — a stale older-version
    # session must not overwrite a current render. Missing/unparseable stamp → not a
    # downgrade, so we proceed (upgrading an old, unstamped board is fine).
    if guard_downgrade and _semver_gt(_existing_board_version(out), version):
        return out
    os.makedirs(paths.data_dir(), exist_ok=True)
    # Export this machine's feed alongside the board (<data_dir>/feeds/self.js, plus the
    # archive shard when there is one). Written AFTER the downgrade guard so a refused
    # passive refresh touches nothing at all. Read-only over the store and fully guarded:
    # a feed hiccup must never cost the user their board.
    try:
        import feeds as _feeds
        _self_mod = sys.modules.get(__name__)
        if _self_mod is None:
            # importlib-loaded (tests use spec_from_file_location without registering in
            # sys.modules), so hand feeds a namespace view of this module's globals.
            import types as _types
            _self_mod = _types.SimpleNamespace(**globals())
        _feeds.export_self_feed(_self_mod, paths.data_dir())
    except Exception:
        pass
    tmp = out + ".tmp." + str(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_doc)
    os.replace(tmp, out)
    # Write the revision hash to a sibling board.rev.js (same dir, board.html basename
    # swapped for board.rev.js) so the change-driven poll can detect a real data change.
    # It is a JS sidecar — `window.__TSREV="<rev>";` — loaded via a dynamically-created
    # <script>, NOT fetch: file:// pages (Safari AND Chrome) BLOCK fetch-ing local files
    # but DO load local <script> resources, so this works with no server. A dynamically-
    # loaded subresource also does NOT trigger the top-level loading bar. Written
    # unconditionally on every regen, incl. the Stop-hook --refresh-if-live path: an
    # unchanged data set yields the same hash, so the page won't reload.
    revpath = os.path.join(os.path.dirname(out), "board.rev.js")
    rtmp = revpath + ".tmp." + str(os.getpid())
    with open(rtmp, "w", encoding="utf-8") as f:
        f.write("window.__TSREV=%s;" % json.dumps(rev))
    os.replace(rtmp, revpath)
    return out


def maybe_refresh_board():
    """Best-effort, silent board regen — only when autorefresh is on AND board.html
    already exists (the user has opened it). Same gate as `board --refresh-if-live`.
    Never creates the file for a non-board user, never prints, never raises.

    Called synchronously at every status-transition (#399) so the persisted board
    never shows stale status between a mutation and the turn-end Stop hook."""
    try:
        import config
        if not config.board_autorefresh_enabled():
            return
        if not os.path.exists(os.path.join(paths.data_dir(), "board.html")):
            return
        # passive refresh: never let a stale older-version session downgrade a newer board.
        write_board(guard_downgrade=True)
    except Exception:
        pass


def cmd_board(a):
    """`task-station board [--open]` — write the HTML board and print its path.

    `--refresh-if-live` is the Stop-hook path: regenerate board.html ONLY when board
    auto-refresh is opted in AND the file already exists (the user has opened the
    board at least once). Best-effort and silent — never creates the file for someone
    who never uses the board, never prints, never raises. Gated (in the shared
    maybe_refresh_board helper) so the gating is unit-testable."""
    if getattr(a, "refresh_if_live", False):
        maybe_refresh_board()
        return
    out = write_board()
    print(out)
    if getattr(a, "open", False):
        _open_path(out)


def _task_cat_tag(task):
    """The bare category TAG for a task ('FEATURE'…) or '' — for auto-attach scoring."""
    try:
        import categories
        meta = categories.hub_meta(task.get("color")) if task.get("color") else None
        return (meta or {}).get("tag") or ""
    except Exception:
        return ""


def _brain_signals(task, session=None):
    """The auto-attach signal bundle for a task (see brains.score_brains). Reads only the
    task blob + the session's recorded cwd + an optional skill hint env — never the LLM."""
    cwd = ""
    if session:
        try:
            cwd = (task.get("session_meta", {}).get(session) or {}).get("cwd") or ""
        except Exception:
            cwd = ""
    if not cwd:
        try:
            cwd = os.getcwd()
        except Exception:
            cwd = ""
    text = " ".join(x for x in (task.get("title") or "", task.get("summary") or "",
                                task.get("goal") or "") if x)
    return {"repos": list(task.get("projects") or []), "cwd": cwd, "text": text,
            "category": _task_cat_tag(task),
            "skill": os.environ.get("TASK_STATION_SKILL_HINT", "")}


def _brain_task_views():
    """Light per-task views for brains.derive (read-only tally; never writes the store):
    `{uuid, status, title, category, signals:{prs,stories}, updated_ts}` for every task."""
    try:
        import feeds as _feeds
    except Exception:
        _feeds = None
    out = []
    for t in sorted_tasks():
        prs = [(_feeds._pr_signal_id(p.get("url")) if _feeds else p.get("url"))
               for p in merged_prs(t)]
        out.append({"uuid": t.get("id") or "", "status": task_status(t),
                    "title": t.get("title") or "", "category": _task_cat_tag(t),
                    "signals": {"prs": [p for p in prs if p],
                                "stories": [s.get("id") for s in _story_refs(t) if s.get("id")]},
                    "updated_ts": t.get("updated_ts") or 0})
    return out


def auto_attach_brain(task, session=None):
    """AUTO-attach `task` to its best-scoring brain (F4) — the user never names one. Scores
    every brain from the task's signals and, when a scored brain clears the threshold,
    writes the winner to brains.json ONLY if the task is still on 'main' and not pinned
    (auto_assign's rule). Fail-open: any hiccup leaves the task on 'main'. Returns the
    resolved brain name ('main' when nothing scored or on error)."""
    try:
        import brains as _brains
        dd = paths.data_dir()
        cfg = _brains.load(dd)
        tid = task.get("id") or ""
        if _brains.is_pinned(cfg, tid) or _brains.brain_for(cfg, tid) != _brains.DEFAULT_BRAIN:
            return _brains.brain_for(cfg, tid)
        winner = _brains.score_brains(cfg, _brain_signals(task, session)).get("winner")
        if winner and winner != _brains.DEFAULT_BRAIN and _brains.auto_assign(cfg, tid, winner):
            _brains.save(cfg, dd)
            return winner
        return _brains.brain_for(cfg, tid)
    except Exception:
        return "main"


def cmd_brains(a):
    """`task-station brains <action> …` — view/edit the Interbrain brains & sharing
    config in <data_dir>/brains.json (a NEW additive file; NEVER touches tasks.db).
    Actions: list · add <name> [--description/--purpose/--keywords/--repos/
    --category-affinity] · edit <name> [same flags] · rename <a> <b> · archive <name> ·
    share <brain> --with <alias|org> [--tag TAG] · unshare … · assign <task-ref> <brain>
    (manual override — pins) · suggest --task <ref> (the scoring audit table) · show
    [<name>]. This CLI is the ONLY write path for brains.json — a `file://` board page
    cannot write, so any UI can only emit these commands for a human to run."""
    import brains as _brains
    dd = paths.data_dir()
    act = (getattr(a, "action", None) or "show")
    rest = list(getattr(a, "args", None) or [])
    cfg = _brains.load(dd)
    if act == "list":
        for b in _brains.list_brains(cfg):
            sh = ", ".join((r["with"] + (":" + r["tag"] if r.get("tag") else ""))
                           for r in b["shares"]) or "—"
            print("%s%s  shares: %s" % (b["name"],
                  " (archived)" if b["archived"] else "", sh))
        return
    if act == "suggest":
        ref = getattr(a, "task", None) or (rest[0] if rest else None)
        t = resolve_ref(ref) if ref else None
        if not t:
            print("brains suggest: pass --task <ref> (the task to score)")
            return
        res = _brains.score_brains(cfg, _brain_signals(t))
        print("brains suggest — task #%s [%s] %s (threshold %d):"
              % (t.get("seq", t["id"][:8]), t["id"][:8], t.get("title") or "",
                 res["threshold"]))
        print("  %-16s %5s  %s" % ("brain", "score", "repo/keyword/category/skill"))
        for r in res["scores"]:
            bd = r["breakdown"]
            flag = " ←winner" if r["name"] == res["winner"] else ""
            arch = " (archived)" if r.get("archived") else ""
            print("  %-16s %5d  %d/%d/%d/%d%s%s"
                  % (r["name"], r["total"], bd["repo"], bd["keyword"],
                     bd["category"], bd["skill"], arch, flag))
        cur = _brains.brain_for(cfg, t["id"])
        print("  → would attach to: %s (currently: %s%s)"
              % (res["winner"], cur, ", pinned" if _brains.is_pinned(cfg, t["id"]) else ""))
        return
    if act == "show":
        views = _brain_task_views()
        want = rest[0] if rest else None
        counts = {}
        for v in views:
            br = _brains.brain_for(cfg, v["uuid"])
            counts[br] = counts.get(br, 0) + 1
        derived = {}
        for b in _brains.list_brains(cfg):
            if want and b["name"] != want:
                continue
            derived[b["name"]] = _brains.derive(cfg, b["name"], views)
        out = {"brains": ({want: cfg["brains"].get(want)} if want else cfg["brains"]),
               "assign_count": len(cfg["assign"]), "pinned_count": len(cfg.get("pinned") or {}),
               "task_counts": counts, "derived": derived}
        print(json.dumps(out, indent=2, sort_keys=True))
        return
    changed = False
    _field_kwargs = {k: getattr(a, k, None)
                     for k in ("description", "purpose", "keywords", "repos", "category_affinity")}
    if act == "add":
        changed = _brains.add(cfg, rest[0], now=_now(), **_field_kwargs) if rest else False
    elif act == "edit":
        changed = _brains.edit(cfg, rest[0], **_field_kwargs) if rest else False
    elif act == "rename":
        changed = _brains.rename(cfg, rest[0], rest[1]) if len(rest) >= 2 else False
    elif act == "archive":
        changed = _brains.archive(cfg, rest[0]) if rest else False
    elif act == "share":
        changed = _brains.share(cfg, rest[0] if rest else "",
                                getattr(a, "with_", None), getattr(a, "tag", None))
    elif act == "unshare":
        changed = _brains.unshare(cfg, rest[0] if rest else "",
                                  getattr(a, "with_", None), getattr(a, "tag", None))
    elif act == "assign":
        # Manual override — pins the assignment so the scorer respects it forever.
        if len(rest) >= 2:
            t = resolve_ref(rest[0])
            if not t:
                print("brains: no task matches %r" % rest[0])
                return
            changed = _brains.assign(cfg, t["id"], rest[1], pinned=True)
    else:
        print("brains: unknown action %r" % act)
        return
    if changed:
        _brains.save(cfg, dd)
        print("brains: %s ok" % act)
    else:
        print("brains: %s — no change" % act)


def cmd_hook_health(a):
    """`task-station hook-health [--clear]` — what the hooks failed at.

    Hooks are deliberately non-fatal, so a broken call used to be invisible;
    `hooks/_ts_lib.sh::ts_run` records each one instead. This is the human view of
    that log, and `--clear` is the acknowledgement (it also re-arms the nag)."""
    if getattr(a, "clear", False):
        n = hook_health.clear()
        print("Cleared %d hook-failure record(s) from %s."
              % (n, hook_health.log_path()))
        return
    rows = hook_health.summary()
    if not rows:
        print("No hook failures recorded (%s)." % hook_health.log_path())
        return
    print("Hook failures — newest last (%s):" % hook_health.log_path())
    print("\n".join("  " + r for r in rows))
    print("Hooks stayed non-fatal throughout. Clear with `task-station hook-health --clear`.")


def _heal_positional_ref(a):
    """Fold a POSITIONAL task ref into `--task`, or return the refusal saying why it
    cannot be folded. None when there is nothing to fold.

    WHY THE POSITIONAL EXISTS. `commands/heal.md` runs
    `task-station.py heal --scan --session <sid> $ARGUMENTS`, so a user typing `/heal 12`
    hands this CLI a bare `12`. The heal subparser had no positional, so argparse exited
    with `unrecognized arguments: 12` and the command died before the scan ever ran — the
    ONE form a person actually types was the one form that could not work. The fix cannot
    live in the command file, either: `$ARGUMENTS` legitimately carries `--task <n>`,
    `--all`, or nothing at all, and there is no word to insert there that parses all four.

    ONE RESOLVER, NOT TWO. This only ever COPIES the ref into `--task`; `_heal_targets`
    still does every lookup, so a seq, a hub ordinal and an id prefix behave identically
    whichever way they were typed and there is no second resolution path to drift.

    TWO REFUSALS, and both of them are "do not guess which record you meant":

      * WITH `--all`. One names a single task and the other sweeps the board. Silently
        picking either would reconcile a scope nobody asked for, and dropping the ref
        without a word is how someone comes to believe they healed one task when they
        swept the whole board.
      * WITH A `--task` NAMING SOMETHING ELSE. A precedence rule would be invisible at
        the call site and the cost of it being wrong is a reconcile written onto the
        wrong task. The SAME ref twice is accepted instead of refused: `/todo heal 12`
        fills both slots from one word, and two spellings of one task is not a conflict.
        Compared case-insensitively, because an id prefix typed in either case still
        resolves to exactly the same record."""
    ref = str(getattr(a, "ref", None) or "").strip()
    if not ref:
        return None
    if getattr(a, "all", False):
        return ("heal: `%s` names ONE task and `--all` sweeps every open task, so the two "
                "cannot be combined — guessing the scope is how the wrong record gets "
                "reconciled. Pass one or the other. Nothing was read." % ref)
    named = str(getattr(a, "task", None) or "").strip()
    if named and named.casefold() != ref.casefold():
        return ("heal: `--task %s` and the positional `%s` name different tasks, and "
                "there is deliberately no precedence rule between them — a silent winner "
                "would reconcile a record you did not mean. Pass ONE of them. Nothing was "
                "read." % (named, ref))
    a.task = named or ref
    return None


def _heal_targets(a):
    """The tasks a `heal` invocation acts on, as `(tasks, error_line)`.

    Per-task by DEFAULT — `--task <ref>`, else the attached task. `--all` sweeps every
    open/active task on the board and is the only path that can touch more than one
    record, which is why it warns loudly about its scope before doing anything."""
    if getattr(a, "all", False):
        return [t for t in sorted_tasks() if is_on_board(t)], None
    ref = getattr(a, "task", None)
    if ref:
        task = resolve_ref(ref) or load_task(ref)
        if not task:
            return [], "No task matching '%s'.\n\n%s" % (ref, _format_list())
        return [task], None
    task = _session_task(getattr(a, "session", None))
    if not task:
        return [], ("No task attached — `heal --task <n>` for a specific task, or "
                    "`heal --all` to sweep the board.")
    return [task], None


def _heal_scan_one(task, probe_branches=True, link_probe=None):
    """Run the layer-1 scan for one task and persist its gate file. Never mutates the
    task. `probe_branches` wires the git prober (off for the cheap SessionStart path)."""
    bp = _heal.branch_prober(task) if probe_branches else None
    result = _heal.scan(task, branch_probe=bp, link_probe=link_probe)
    _heal.write_gate(result)
    return result


def _heal_scan_report(task, result):
    """`heal --scan` output for one task: the nine finding checks, each reported clean
    or with its hits, plus the health metric that is the tenth. Zero tokens of
    judgment — this is the deterministic layer, printed verbatim.

    Below the checks sit the sections that are NOT checks: the MERGE CANDIDATES proposed
    from their leading shape, the expected-ephemeral count, the PINNED set to re-read, the
    GOAL LINE with what has landed since it was written, and what has ACCRUED since the
    last heal. None is a defect, so none reaches `Heal due?` — that line is computed from
    the findings alone, and mixing these in would put YES on a perfectly reconciled task.

    THE ACCRUAL SECTION IS PRINTED HERE ON PURPOSE, not only in the dry run. A clean scan
    is where the skill STOPS (stamp it and report), so a gap named only in the dry run is
    one nobody sees on a clean task — and the incident that added it was a task that
    scanned clean on every check while a shipped release sat recorded nowhere on it.

    IT CLOSES ON THREE ROWS, NOT ONE (`heal.summary_lines`): what the machine checked,
    whether the half it cannot check has been recorded, and only then the verdict. A lone
    `Heal due? no` reads as "this task is a complete record", which is the reading that
    let both of the incidents above pass for healthy."""
    seq = task.get("seq", task["id"][:8])
    out = ["[HEAL-SCAN] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"])]
    out.append("  %-28s %s" % ("Health", _heal.health_line(result.get("health") or {})))
    out.extend(_heal.scan_lines(result))
    # Proposals, information and counts, BELOW the findings and never mixed into them:
    # none of these is a defect, and `Heal due?` below is computed from the findings
    # alone.
    out.extend(_heal.merge_candidate_lines(result))
    out.extend(_heal.ephemeral_lines(result))
    out.extend(_heal.pinned_lines(result))
    out.extend(_heal.goal_review_lines(result))
    out.extend(_heal.accrual_lines(result))
    out.extend(_heal.summary_lines(task, result))
    return "\n".join(out)


def _heal_block(task, result, ops, applied=None, backup=None, before=None):
    """The model-facing [HEAL] block — layer 2's brief.

    Carries the scan findings, the health metric, the FULL current decision set (the
    pass cannot reconcile what it cannot see), the mechanical plan, and the exact verb
    commands for the judgment calls a machine must not make on its own. `applied` is
    None on a dry run and the `(lines, n, skipped)` triple after an --apply.

    IT ALSO CARRIES THE GOAL LINE AND THE LIVE CHECKLIST, printed together right after
    the decision set. They were absent for the same reason the checks never looked at
    them: all three verbs here are DECISION verbs. But the goal says what DONE looks
    like and the checklist says what to DO, so they are what a cold session reads FIRST,
    while the decisions mostly say WHY — and on one real task both had been overtaken
    while every check reported clean. They sit BESIDE the newest decisions on purpose:
    those decisions are the evidence that retires them, and the one question this pass
    must ask of each is whether it does."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("%s. Reconcile this task's APPEND-ONLY decision log into CURRENT STATE."
               % ("APPLIED — the mechanical plan below has been performed"
                  if applied else "DRY RUN — nothing has been changed"))
    out.append("")
    if before:
        out.append("HEALTH BEFORE: %s" % _heal.health_line(before))
    out.append("HEALTH: %s" % _heal.health_line(h))
    out.append("")
    out.append("SCAN (layer 1 — deterministic, no judgment):")
    out.extend(_heal.scan_lines(result))
    out.extend(_heal.merge_candidate_lines(result))
    out.extend(_heal.ephemeral_lines(result))
    out.extend(_heal.pinned_lines(result))
    out.extend(_heal.goal_review_lines(result))
    out.extend(_heal.accrual_lines(result))
    out.extend(_heal.summary_lines(task, result))
    out.append("")
    out.append("MECHANICAL PLAN (what --apply does on its own):")
    out.extend(_heal.plan_lines(ops))
    if applied:
        lines, n, skipped = applied
        out.append("")
        out.append("APPLIED %d operation(s), skipped %d:" % (n, skipped))
        out.extend("  • %s" % ln for ln in (lines or ["(none)"]))
        if backup:
            out.append("  Backup of the pre-heal task blob: %s" % backup)
        out.append("  STAMPED: this task now records that a heal happened (%s), so the "
                   "next scan counts new decisions from HERE instead of reporting the "
                   "whole log as unreconciled." % _heal.heal_stamp_line(h))
        out.append("  NOTHING was deleted. Every original is still in "
                   "`/todo %s history`, marked with what replaced it." % seq)
        undo = _heal.undo_lines(ops)
        if undo:
            out.append("")
            out.extend(undo)
            if backup:
                out.append("  • WHOLE-TASK FALLBACK, for anything above with no verb of "
                           "its own: the pre-heal blob at %s." % backup)
    out.append("")
    out.append("CURRENT DECISIONS (%d) — reconcile THESE:" % h.get("decisions_current", 0))
    for i, d in _dec.live(task.get("decisions")):
        txt = _dec.text(d)
        out.append("  %2d. %s%s" % (i, DECISION_PIN_MARK if _dec.is_pinned(d) else "", txt))
    if not h.get("decisions_current"):
        out.append("  (none current)")
    # The GOAL and the CHECKLIST, immediately below the decisions and immediately below
    # the NEWEST of them, because the newest evidence is what retires them. One question
    # for both, and it is the question no check can ask.
    live_steps = _steps.live(task.get("steps"))
    goal = str(task.get("goal") or "").strip()
    out.append("")
    out.append("THE GOAL LINE — does the newest evidence above retire this?")
    out.append("  %s" % (goal or "(none set)"))
    out.append("")
    out.append("THE LIVE CHECKLIST (%d step(s)) — for EACH ONE: does the newest evidence "
               "above retire this?" % len(live_steps))
    for i, s in live_steps:
        out.append("  %s %2d. %s" % ("✓" if _steps.is_done(s) else "☐", i,
                                     _steps.text(s)))
    if not live_steps:
        out.append("  (no steps)")
    out.append("  WHY THESE TWO ARE HERE. A cold session reads the goal and the checklist "
               "FIRST — they say what DONE looks like and what to DO, while the decisions "
               "above mostly say WHY. A decision that was right when written and was "
               "later refuted by REALITY leaves nothing inconsistent behind, so no check "
               "can see that the goal describes a mission already accomplished or that a "
               "step names work a superseded decision retired. One real task held both "
               "while all its checks reported clean, and the checklist went on reading as "
               "the plan. Where the wording overlapped enough to spot mechanically, the "
               "scan lists it above as a STEP RESTATING A SUPERSEDED DECISION — a "
               "PROVISIONAL finding that still needs your reading.")
    out.append("")
    out.append("NOW DO THE JUDGMENT WORK — the part a machine must not guess at. For "
               "each item below, decide and act; do NOT invent history, and NEVER "
               "delete a decision (no verb can):")
    out.append("  1. SUPERSEDE what is now WRONG — `update --task %s --decision "
               "'<the correct call + why>' --supersedes <n>` (repeatable). Use this "
               "only when something refuted it." % seq)
    out.append("  2. SPLIT what is COMPOUND — a decision mixing still-valid rulings "
               "with refuted ones cannot be superseded without destroying the good "
               "half. Add the atomic parts with `--decision` (one call each), note "
               "their numbers, then run `heal --split <n> --into <n1,n2,…> --task %s`." % seq)
    out.append("  3. MERGE what is TRUE BUT NO LONGER LOAD-BEARING — release records, "
               "iteration steps, process-error corrections. Any MERGE CANDIDATES listed "
               "in the scan above are the mechanical starting point: groups that open "
               "with the same shape. They are proposals, not findings — read each group "
               "and decide. Add the one summary decision, then `heal --merge <n1,n2,…> "
               "--into <n> --task %s`. Do NOT supersede these: nothing refuted them, and "
               "saying so would put a lie in the record. A RE-FRAGMENTED CONSOLIDATION "
               "(check 9, and a FINDING rather than a proposal) is the same verb with the "
               "consolidation itself folded in: an earlier pass already ruled that "
               "subject was one entry, so write ONE updated summary covering the strays "
               "too and merge the old consolidation in with them." % seq)
    out.append("  4. RETRO-DISPOSE every undispositioned ack — `heal --apply --task %s "
               "--dispose-acks <id8,…|all> --noop '<why nothing was needed>'` (or "
               "--decision '<what it changed>' / --memory <slug>). `all` is legitimate "
               "here: those acks were made by sessions that no longer exist, so one bulk "
               "--noop with an honest reason IS the correct disposition. Every "
               "retro-fill is marked RETRO with who filled it and when — the original "
               "ack's session and timestamp are never rewritten." % seq)
    out.append("  5. RETIRE any STALE STEP — `update --task %s --step-add '<the "
               "corrected step>' --step-supersede <n>`. The stale step leaves the "
               "checklist and BOTH sides of the n/m count, keeps its text in history "
               "marked with what replaced it, and `--step-restore <n>` undoes it. Do NOT "
               "tick it done (nobody did it) and do NOT add a warning step about it." % seq)
    out.append("  6. REWRITE the `state` line if it drifted — `update --task %s "
               "--state 'NEXT: <concrete first move> — <current standing>'`. Fix a "
               "drifted `--summary` the same way." % seq)
    out.append("  7. RE-READ every PINNED decision listed in the scan above. A pin puts "
               "that entry at the head of EVERY session's digest, so a line that has "
               "quietly gone stale in one costs more than the same line anywhere else — "
               "one real task briefed sessions for days with two codenames a later "
               "decision had already retired. Nothing here is a defect on its own; the "
               "check is whether it is still ACCURATE. If it is not, supersede it (1) or "
               "split it (2); if it no longer needs to lead, `update --task %s "
               "--unpin-decision <n>`." % seq)
    out.append("  8. RE-READ THE GOAL LINE AND EVERY LIVE STEP — both are printed below "
               "the decision set, and the question for each is the same: DOES THE NEWEST "
               "EVIDENCE RETIRE THIS? A goal describing a mission the record now shows as "
               "accomplished, and a step naming work a superseded decision retired, are "
               "read as INSTRUCTIONS by the next cold session, and neither leaves anything "
               "inconsistent behind for a check to catch. Rewrite a drifted goal with "
               "`update --task %s --goal '<what done looks like now>'`; retire an "
               "overtaken step with `update --task %s --step-add '<the corrected step>' "
               "--step-supersede <n>`. The GOAL REVIEW line in the scan says how many "
               "decisions have landed since the goal was last written — a count, not an "
               "accusation. Any STEP RESTATING A SUPERSEDED DECISION listed above IS a "
               "finding, but a PROVISIONAL one: read the step and the decision together, "
               "because the step written to RECORD a retirement uses the same words as "
               "the step that still orders it." % (seq, seq))
    out.append("  9. Prose that CLAIMS a supersession must become structure — the "
               "digest cannot act on a sentence saying \"decision 4 was wrong\".")
    out.append(" 10. VERIFY THAT EVERYTHING WHICH ACTUALLY SHIPPED SINCE THE LAST HEAL "
               "HAS A DECISION — a release, a merged PR, a document. This is the ONE gap "
               "the deterministic layer STRUCTURALLY cannot cover, and the third thing "
               "on this list that no check will ever raise (the others are 7, whether a "
               "pinned decision is still accurate, and 8, whether the goal and the "
               "checklist have been overtaken). Every check works by "
               "cross-referencing two things the task itself holds; work that is recorded "
               "NOWHERE on it leaves nothing to cross-reference, so the scan cannot tell "
               "that apart from nothing having happened. The ACCRUED line above says how "
               "much has been recorded since the stamp — check it against what you know "
               "shipped, from the conversation and the repo, and record whatever is "
               "missing with `update --task %s --decision '<what shipped + why>'` (and "
               "`--pr <url>` / `--log '<vX.Y.Z shipped: what>'`). A clean scan means the "
               "record does not contradict itself; it does NOT mean the record is "
               "complete." % seq)
    out.append("")
    out.append("OUT OF SCOPE: do NOT touch the `--log` milestone trail or `history` — "
               "they are append-only and sacred. No verb rewrites a step or a decision "
               "in place: supersede it and add the corrected one.")
    if applied is None:
        out.append("")
        if [o for o in ops if not o.get("manual")]:
            out.append("This was a DRY RUN — the default. Nothing changed. Run `heal "
                       "--apply --task %s` to perform the mechanical plan (it backs the "
                       "task blob up first), or just work the judgment list above by "
                       "hand. `--apply` prints ONLY what it did and does NOT reprint "
                       "this block — reprinting it is what used to make one heal cost "
                       "two." % seq)
        else:
            out.append("This was a DRY RUN — the default, and there is nothing mechanical "
                       "to apply. A bare `heal --apply --task %s` would perform zero "
                       "operations, so it REFUSES rather than stamping a heal that never "
                       "happened. Work the judgment list above, then record the pass "
                       "below." % seq)
        out.append("Reconciled it by JUDGEMENT alone and nothing needed changing? Record "
                   "that with `heal --mark-healed --task %s --note '<what you checked>'` "
                   "— otherwise the record still says this task has never been healed, "
                   "and every session opens on a false alarm." % seq)
    out.append("    " + _cli_fallback())
    return "\n".join(out)


def _heal_applied_block(task, result, applied, backup, ops=None, before=None):
    """What `--apply` prints: ONLY what it did.

    THE COST THIS EXISTS TO STOP. `--apply` used to re-render the entire dry run — the
    scan block, the merge candidates, the pinned set, every current decision, and the
    nine-item judgment list — with the applied lines bolted on. Measured on one small
    task the two blocks were 4,021 and 4,174 characters, i.e. the same block twice; on a
    real 40-decision task the dry run is ~47,000 characters and **94% of it is the
    decision list**. So the obvious two-step — read `heal`, then run `heal --apply` —
    paid ~12,000 tokens TWICE for ONE heal, and the second copy told the caller nothing
    it had not just read.

    So this carries the things only the apply knows: what was performed, what was
    skipped, where the backup went, HOW TO TAKE EACH WRITE BACK, and what the task looks
    like now against what it looked like before. The scan, the decision list and the
    judgment list are deliberately absent — `--verbose` renders the full `_heal_block`
    for anyone who wants them.

    THE UNDO SECTION IS WHAT REPLACES THE APPROVAL GATE. `/heal` no longer stops to ask
    before applying, so this report is the only place a wrong call can be caught — and
    "every heal is reversible" is not enough to catch one with, because acting on it
    means first working out which decision numbers moved. `heal.undo_lines` prints the
    exact command per performed op instead, and says so plainly for the retro-disposition,
    which has no inverse verb at all.

    `before` is the pre-apply health dict; with it the report reads BEFORE → NOW rather
    than leaving the reader to remember a number from the previous block."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    lines, n, skipped = applied
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("APPLIED %d operation(s), skipped %d. This report is ONLY what the apply "
               "did — the scan, the current decisions and the judgment list are not "
               "reprinted, because you just read them in the dry run and the decision "
               "list alone is ~94%% of that block. `heal --apply --verbose --task %s` "
               "prints the whole thing." % (n, skipped, seq))
    out.append("")
    out.append("WHAT IT DID:")
    out.extend("  • %s" % ln for ln in (lines or ["(none)"]))
    if backup:
        out.append("  Backup of the pre-heal task blob: %s" % backup)
    out.append("  STAMPED: this task now records that a heal happened (%s), so the next "
               "scan counts new decisions from HERE instead of reporting the whole log "
               "as unreconciled." % _heal.heal_stamp_line(h))
    out.append("  NOTHING was deleted. Every original is still in `/todo %s history`, "
               "marked with what replaced it." % seq)
    undo = _heal.undo_lines(ops)
    if undo:
        out.append("")
        out.extend(undo)
        if backup:
            out.append("  • WHOLE-TASK FALLBACK, for anything above with no verb of its "
                       "own: the pre-heal blob at %s is the task exactly as it stood "
                       "before this run." % backup)
    out.append("")
    if before:
        out.append("HEALTH BEFORE: %s" % _heal.health_line(before))
    out.append("HEALTH NOW: %s" % _heal.health_line(h))
    is_due, reasons = _heal.due(task, result=result)
    out.append("STILL OUTSTANDING: %s"
               % (("%s — the judgment work is not done; `heal --scan --task %s` shows it"
                   % ("; ".join(reasons), seq)) if is_due
                  else "nothing the scan can see — no finding, no heal due. It reads the "
                       "RECORD only, so confirm separately that everything which actually "
                       "shipped since the last heal has a decision: that is the one gap "
                       "no check here can cover"))
    return "\n".join(out)


def _heal_no_operations_block(task, result, ops, attempted=None):
    """What a `--apply` that has NOTHING to perform prints instead of a stamp.

    THE BUG THIS CLOSES. A bare `--apply` on a task with an empty mechanical plan
    performed zero operations, honestly said `APPLIED 0 operation(s)` — and then stamped
    the heal anyway, so the record claimed the task had been reconciled when nothing had
    happened. It did not silence the nag (real findings still make a heal due), but the
    timestamp was a lie, and it is exactly the command someone runs when they assume
    `--apply` IS the heal rather than the mechanical subset of one.

    A stamp that is sometimes false is worse than the always-on alarm the stamp was
    added to fix, because it makes every OTHER stamp unreadable. So this refuses, changes
    nothing, writes no backup, and names the two moves that are actually true.

    `attempted` is the run's own report lines when the plan HAD operations and every one
    of them failed — a different story from an empty plan, and the reader needs to see
    which one happened."""
    seq = task.get("seq", task["id"][:8])
    h = result.get("health") or {}
    out = []
    out.append("[HEAL] Task #%s [%s] — %s" % (seq, task["id"][:8], task["title"]))
    out.append("REFUSED: --apply performed no operation, so nothing was changed and "
               "NO heal was stamped. A stamp says this task WAS reconciled; writing one "
               "for a pass that did nothing would put a false `last heal just now` in the "
               "record, and one stamp that lies makes every other stamp unreadable.")
    out.append("")
    if attempted:
        out.append("EVERY PLANNED OPERATION FAILED — nothing was written:")
        out.extend("  • %s" % ln for ln in attempted)
    else:
        out.append("MECHANICAL PLAN (what --apply would have performed):")
        out.extend(_heal.plan_lines(ops))
    out.append("")
    out.append("TWO REAL OPTIONS — take whichever is true:")
    out.append("  1. THERE IS WORK, so name it. `heal --apply --task %s --dispose-acks "
               "<id8,…|all> --noop '<why nothing was needed>'` retro-fills old acks; "
               "`heal --split <n> --into <n1,n2,…> --task %s` and `heal --merge "
               "<n1,n2,…> --into <n> --task %s` record a split or a merge (add the "
               "replacement decisions with `update --decision` first). The judgment "
               "verbs need no --apply at all — `update --task %s --decision '<the "
               "correct call>' --supersedes <n>` and `update --task %s --step-add "
               "'<the corrected step>' --step-supersede <n>` write immediately."
               % (seq, seq, seq, seq, seq))
    out.append("  2. YOU RECONCILED IT BY JUDGEMENT and nothing needed changing: `heal "
               "--mark-healed --task %s --note '<what you checked>'`. That is the honest "
               "record of this pass, and it is what stamps." % seq)
    out.append("")
    out.append("HEALTH: %s" % _heal.health_line(h))
    out.extend(_heal.summary_lines(task, result))
    return "\n".join(out)


def _heal_verb(a):
    """The explicit `heal --split N --into …` / `heal --merge N,M --into N` verbs.

    These exist so the LLM pass can act on ITS OWN reading of the content — it adds the
    atomic parts (or the one summary) with `update --decision`, then names the mapping
    here. Returns a result line. Non-destructive and reversible like every other path:
    the original is marked, never removed."""
    if getattr(a, "all", False):
        return ("heal: --split/--merge name decision numbers on ONE task, so they cannot "
                "be combined with --all. Target it with `--task <n>`.")
    tasks, err = _heal_targets(a)
    if err:
        return err
    task = tasks[0]
    entries = task.get("decisions") or []
    if not entries:
        return "heal: task #%s has no decisions to reconcile." % task.get("seq")
    into = _split_int_list(getattr(a, "into", None))
    msgs = []
    split_ref = getattr(a, "split", None)
    merge_ref = getattr(a, "merge", None)
    if split_ref is not None:
        if not into:
            return ("heal --split %s: pass `--into <n1,n2,…>` naming the decisions it "
                    "became (add them first with `update --decision`)." % split_ref)
        ok, e = _dec.mark_split(entries, split_ref, into)
        msgs.append(("split decision %s into %s — the original is kept in history, "
                     "marked, and `update --restore-decision %s` undoes it"
                     % (split_ref, ", ".join(str(n) for n in into), split_ref))
                    if ok else "heal: %s" % e)
    if merge_ref is not None:
        members = _split_int_list(merge_ref)
        if len(into) != 1:
            return ("heal --merge %s: pass `--into <n>` naming the ONE decision that "
                    "absorbed them (add it first with `update --decision`)." % merge_ref)
        if not members:
            return "heal --merge: name the decisions to merge, e.g. `--merge 3,7,9`."
        done = []
        for n in members:
            ok, e = _dec.mark_merged(entries, n, into[0])
            if ok:
                done.append(n)
            else:
                msgs.append("heal: %s" % e)
        if done:
            # The indices are known HERE, so the undo names them. This path writes
            # immediately and takes no backup, so a generic `<n>` would leave the reader
            # reconstructing which numbers moved at the one moment they need them.
            msgs.append("merged %s into %d — each original is kept in history, marked, "
                        "and `update --task %s %s` undoes it (the flag repeats; it does "
                        "not take a list)"
                        % (", ".join(str(n) for n in done), into[0],
                           _heal._task_ref(task), _heal._restore_flags(done)))
    if not msgs:
        return "heal: nothing to do (pass --split or --merge with --into)."
    task["decisions"] = entries
    # A split or a merge IS reconciliation, so it stamps — same rule as any other
    # operation-performing --apply. This path used to return without stamping, which is
    # why seventeen merges on a real task still left it reading `last heal never`: the
    # stamp lived only on the generic --apply path, and the verb path returns before it.
    _heal.stamp_healed(task, kind=_heal.HEAL_KIND_APPLY)
    task["updated_ts"] = _now()
    save_task(task)
    maybe_refresh_board()
    return "\n".join(msgs)


def _split_str_list(raw):
    """`"ab12cd34, 9e01f2aa"` / `"all"` / `["ab12cd34"]` → a list of trimmed strings.
    The string counterpart of `_split_int_list`, for `--dispose-acks` (memo id8s, or the
    single word `all`)."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", ",").split(",")
    return [str(v).strip() for v in items if str(v).strip()]


def _split_int_list(raw):
    """`"3,7, 9"` / `3` / `[3,7]` → `[3, 7, 9]`, dropping anything uncoercible."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", ",").split(",")
    out = []
    for v in items:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _heal_mark(a, tasks):
    """`heal --mark-healed [--note '<why>']` — record a JUDGEMENT-ONLY reconcile.

    The gap this closes: the mechanical plan is often empty ("nothing mechanical to do —
    any remaining work needs judgment"), so a reconciler who read every decision and
    concluded nothing needed changing had no way to say so. The record then still said
    `last heal never`, every session opened on a false "under-reconciled" alarm, and the
    one signal built to be trusted became the one people learned to ignore.

    This is a WRITE, so it obeys the same rule as `--apply`: back up first, refuse
    without a backup. It does NOT run the mechanical plan — that is exactly the point."""
    out = []
    note = (getattr(a, "note", None) or "").strip()
    for task in tasks:
        path = _heal.backup(task, strip=store.strip_rev)
        if not path:
            out.append("[HEAL] Task #%s — REFUSED: could not write the pre-heal backup "
                       "under %s, so nothing was recorded."
                       % (task.get("seq"), _heal.gate_dir()))
            continue
        _heal.stamp_healed(task, kind=_heal.HEAL_KIND_MARK, note=note)
        task["updated_ts"] = _now()
        save_task(task)
        _heal.clear_gate(task["id"])
        fresh = _heal_scan_one(task, probe_branches=True)
        h = fresh.get("health") or {}
        is_due, reasons = _heal.due(task, result=fresh)
        out.append("[HEAL] Task #%s — MARKED HEALED at %d decision(s)%s. No operation "
                   "was performed: this records that the log was read and reconciled by "
                   "judgement, which is what `--apply` could not say when its plan was "
                   "empty.\n  %s\n  Heal due? %s"
                   % (task.get("seq"), h.get("decisions_total", 0),
                      (" — why: %s" % note) if note else
                      " (no --note given; a why is worth one line)",
                      _heal.health_line(h),
                      ("YES — %s" % "; ".join(reasons)) if is_due else "no"))
    return "\n\n".join(out)


def _heal_dispose(a, task, result):
    """The explicit `--dispose-acks <id8,…|all>` ops for one task, as `(ops, error)`.

    Exactly ONE disposition is required, and they are the same three the live `memo ack`
    takes — `--decision [TEXT]` / `--memory <slug>` / `--noop "<reason>"` — because the
    vocabulary a retro-fill records has to be the vocabulary everything else reads.

    ONE deliberate difference from `memo ack --decision`: this does NOT append a
    decision to the task. A heal records what an ack DID; minting a decision now and
    dating it to a session that no longer exists would be inventing history, which is
    the one thing this pass must never do."""
    disp, err = memo_ack_disposition(decision=getattr(a, "decision", None),
                                     memory=getattr(a, "memory", None),
                                     noop=getattr(a, "noop", None))
    if err:
        return [], ("heal --dispose-acks: %s"
                    % err.split("memo ack: ", 1)[-1])
    pairs, err = _heal.select_acks(task, _split_str_list(getattr(a, "dispose_acks", None)))
    if err:
        return [], err
    if not pairs:
        return [], ("heal --dispose-acks: task #%s has no undispositioned ack to "
                    "retro-fill — the scan reports %d."
                    % (task.get("seq"),
                       _heal.counts(result).get("ack-undispositioned", 0)))
    return _heal.disposition_ops(task, kind=disp["kind"], value=disp["value"],
                                 sid=getattr(a, "session", None), only=pairs), None


def cmd_heal(a):
    """`task-station heal [--task REF] [--scan] [--apply] [--all]` — the reconcile pass.

    task-station has always had capture (`save`) and no reconcile: the decision log only
    grows and nothing ever says "these sixteen entries are now four". The digest no
    longer truncates by age, so this pass is the only thing that keeps it honest — a
    refuted decision briefs every session until a verb marks it. This closes that.

    TWO LAYERS. `--scan` is layer 1: deterministic, zero tokens, and it NEVER mutates
    the task (so it never stamps a heal either — read-only is its whole contract). Bare
    `heal` is layer 2's brief — the findings plus the full decision set plus the exact
    verb commands — and it is a DRY RUN, the default, which changes NOTHING. `--apply`
    performs the mechanical subset of the plan, backs the task blob up first, and
    refuses outright if that backup cannot be written.

    TWO THINGS `--apply` DELIBERATELY DOES NOT DO, both of them measured mistakes:

      * it does not REPRINT the dry run. It reports what it performed, what it skipped,
        where the backup went and how the task reads now — nothing else, because the
        caller just read the rest and ~94% of that block is the decision list. `--apply
        --verbose` restores the full render.
      * it does not STAMP a pass that performed zero operations. That case is REFUSED
        (`_heal_no_operations_block`), naming the two honest moves instead: pass
        operations, or record a judgement-only pass with `--mark-healed --note`.

    THE FLOW IS THE SKILL'S JOB, NOT THE CLI'S. A CLI is one-shot and cannot hold a
    conversation, so dry-run-as-default stays for scripting safety while
    `skills/heal/SKILL.md` orchestrates: scan first, read the dry run at most once,
    judge, execute, stamp, re-scan, report. It no longer stops to ask before applying —
    the approval gate is gone, and what replaces it is the UNDO TRAIL this CLI prints
    (`heal.undo_lines`, and the `update` result line for the two judgement verbs, which
    write immediately and take no backup). A gate is only safe to remove if reversing a
    wrong call costs what approving one did, so every write now names the one command
    that takes it back, with the index it actually touched.

    `--mark-healed` is the judgement-only counterpart, and `--dispose-acks` retro-fills
    the dispositions of acks recorded before dispositions were required.

    THE TASK MAY BE NAMED POSITIONALLY — `heal --scan 12` is `heal --scan --task 12`,
    because `/heal 12` passes that 12 straight through as a positional. The fold and its
    two refusals live in `_heal_positional_ref`, and they run FIRST: a refusal there must
    change nothing, so it has to be answered before any task is loaded."""
    ref_error = _heal_positional_ref(a)
    if ref_error:
        print(ref_error)
        return
    tasks, err = _heal_targets(a)
    if err:
        print(err)
        return
    if not tasks:
        print("No open tasks to heal.")
        return
    scan_only = getattr(a, "scan", False)
    apply_it = getattr(a, "apply", False)
    sweeping = getattr(a, "all", False)
    dispose = _split_str_list(getattr(a, "dispose_acks", None))
    if scan_only and apply_it:
        # `/heal` opens with `--scan` on the caller's behalf (the SKILL drives the rest),
        # so an `--apply` typed alongside it would be silently swallowed by the read-only
        # path and the caller would believe a heal had run. Refuse rather than pick one.
        print("heal --scan is read-only and applies nothing, so the two cannot be "
              "combined. Scan first, then run the operations you decided on — and note "
              "that the `heal` SKILL drives that whole sequence, so you should not need "
              "to type either flag yourself.")
        return
    if getattr(a, "mark_healed", False):
        # A stamp is not a scan and not a plan — refuse the combinations rather than
        # silently picking one, so nobody can think they applied a plan they didn't.
        if (scan_only or apply_it or dispose
                or getattr(a, "split", None) is not None
                or getattr(a, "merge", None) is not None):
            print("heal --mark-healed records a judgement-only heal and performs no "
                  "operation, so it cannot be combined with --scan, --apply, "
                  "--dispose-acks, --split or --merge. Run it on its own, after the "
                  "verbs.")
            return
        if sweeping:
            print("[HEAL] SCOPE: --all covers %d open/active task(s) — %s. Each will be "
                  "BACKED UP and stamped as healed; no decision is touched."
                  % (len(tasks), ", ".join("#%s" % t.get("seq") for t in tasks)))
            print("")
        print(_heal_mark(a, tasks))
        maybe_refresh_board()
        return
    if getattr(a, "split", None) is not None or getattr(a, "merge", None) is not None:
        # `--into` is ONE option, so passing it twice silently keeps only the last value
        # and the other verb links to the wrong target. Refuse rather than mislink: each
        # verb needs its own `--into`, so each needs its own invocation.
        if getattr(a, "split", None) is not None and getattr(a, "merge", None) is not None:
            print("heal: --split and --merge each need their own --into, and --into can only "
                  "be given once — run them as two separate commands.")
            return
        print(_heal_verb(a))
        return
    if dispose and sweeping:
        # An id8 names an ack on ONE task, and `all` means "every undispositioned ack on
        # THIS task" — sweeping the board with one disposition reason would put the same
        # sentence on acks from unrelated work.
        print("heal --dispose-acks names acks on ONE task, so it cannot be combined with "
              "--all. Target it with `--task <n>`.")
        return
    if dispose and scan_only:
        print("heal --scan is read-only, so it cannot dispose of anything. Drop --scan "
              "for the dry run, or add --apply to record the dispositions.")
        return
    # --all is the ONLY path that can touch more than one record. Say so loudly, and
    # say it BEFORE anything happens — including before a mutating --apply.
    if sweeping:
        print("[HEAL] SCOPE: --all covers %d open/active task(s) — %s. %s"
              % (len(tasks), ", ".join("#%s" % t.get("seq") for t in tasks),
                 "Each will be BACKED UP and its mechanical plan APPLIED."
                 if apply_it else "Nothing will be changed (dry run)."))
        print("")
    link_probe = None       # network stays opt-in; a link is never reported dead on a failed check
    blocks = []
    for task in tasks:
        result = _heal_scan_one(task, probe_branches=True, link_probe=link_probe)
        if scan_only:
            blocks.append(_heal_scan_report(task, result))
            continue
        ops = _heal.plan(task, result=result)
        if dispose:
            # An EXPLICIT selection REPLACES the blanket retro-noop the plan proposes for
            # every undispositioned ack. That is what keeps a subset surgical: the acks
            # nobody named stay undispositioned, so the next scan still flags them.
            explicit, derr = _heal_dispose(a, task, result)
            if derr:
                blocks.append(derr)
                continue
            ops = _heal.with_dispositions(ops, explicit)
        if not apply_it:
            blocks.append(_heal_block(task, result, ops))
            continue
        # --apply with NOTHING to perform must REFUSE, not stamp. Checked BEFORE the
        # backup, because a pass that will change nothing has nothing to back up. See
        # `_heal_no_operations_block` for the false `last heal just now` this closes.
        if not [o for o in ops if not o.get("manual")]:
            blocks.append(_heal_no_operations_block(task, result, ops))
            continue
        # --apply: back up FIRST. No backup, no mutation — a reconcile without a
        # backup is the one shape of this feature that could lose work.
        path = _heal.backup(task, strip=store.strip_rev)
        if not path:
            blocks.append("[HEAL] Task #%s — REFUSED: could not write the pre-heal "
                          "backup under %s, so nothing was changed."
                          % (task.get("seq"), _heal.gate_dir()))
            continue
        session = getattr(a, "session", None)

        def _append(text, _t=task, _s=session):
            # Returns the new decision's 1-based index, or None when nothing was
            # appended (append_decision no-ops on blank text). None makes the
            # subsequent mark fail LOUDLY rather than pointing at the wrong entry.
            if not append_decision(_t, text, _s):
                return None
            return len(_t.get("decisions") or [])

        applied = _heal.apply(task, ops, append=_append)
        if not applied[1]:
            # Every planned operation failed at execution — so nothing was performed,
            # and the same rule applies: no stamp. Not saving is what discards the
            # in-memory appends a half-done op left behind (`load_task` rebuilds from
            # the row, so nothing partial survives).
            blocks.append(_heal_no_operations_block(task, result, ops,
                                                    attempted=applied[0]))
            continue
        _heal.stamp_healed(task)
        task["updated_ts"] = _now()
        save_task(task)
        # Re-scan AFTER the mutation so the report and the gate show the healed state,
        # and clear the nag watermark — the state changed, so the nag should re-arm.
        _heal.clear_gate(task["id"])
        fresh = _heal_scan_one(task, probe_branches=True, link_probe=link_probe)
        _stream_emit("task.checkpoint", task, _stream_digest(task), session)
        if getattr(a, "verbose", False):
            blocks.append(_heal_block(task, fresh, ops, applied=applied, backup=path,
                                      before=result.get("health")))
        else:
            # `result` is the PRE-apply scan and `fresh` the post-apply one, so the
            # report can show health before → after without the reader holding a number
            # in their head from the previous block. `ops` carries the per-write undo
            # commands `_heal.apply` recorded as it performed them.
            blocks.append(_heal_applied_block(task, fresh, applied, path, ops=ops,
                                              before=result.get("health")))
    print("\n\n".join(blocks))
    if apply_it and not scan_only:
        maybe_refresh_board()


def cmd_session_start(a):
    task_id = get_link(a.session)
    if task_id == SKIP_SENTINEL:
        return  # session intentionally untracked: stay silent
    # Recent hook failures get ONE line, in the same rail as the memo nag. Self-
    # capping: hook_health stamps the newest failure it reported, so this stays
    # silent until a newer one lands (or `hook-health --clear` re-arms it).
    hh_nag = hook_health.nag()
    task = load_task(task_id) if task_id else None
    if task:
        attach_line = ("[task-station] This session is attached to task [%s] %s (%s). "
                       "Continue it; /done to close."
                       % (task["id"][:8], task["title"], task["status"]))
        olabel = ordinal_label(task, a.session)       # hub '<seq>-<n>' (#463)
        if olabel:
            attach_line += " You are hub session %s." % olabel
        msg = [attach_line]
        msg.extend(cat_lines(task.get("color")))
        adv = ultracode_advisory(task)
        if adv:
            msg.append(adv)
        # Delta-injection: a resuming/attaching session learns what OTHER sessions,
        # workers, and child tasks did to this task while it was away. Bounded to
        # one block; advance the watermark + persist so it's shown exactly once.
        delta = delta_brief(task, a.session)
        if delta:
            msg.append(delta)
            mark_seen(task, a.session)
            save_task(task)
        # Pending memos awaiting THIS session's ack — ack-gated (re-surfaces until
        # acked), so appended alongside the delta but NOT watermark-cleared.
        pending = memo_pending_brief(task, a.session)
        if pending:
            msg.append(pending)
        # Opt-in auto-checkpoint: on a POST-COMPACTION session start, point the model
        # at the durable digest as its source of truth and nudge a refresh if the plan
        # advanced. SessionStart additionalContext reliably lands before the model's
        # next turn — the sanctioned model-facing post-compaction instruction. Gated:
        # auto-checkpoint on + source==compact + attached, else unchanged.
        if getattr(a, "source", "") == "compact" and _auto_checkpoint_enabled():
            seq = task.get("seq", task["id"][:8])
            msg.append("[task-station] Context was just compacted. Task %s's durable "
                       "digest is your source of truth for continuing. If the plan "
                       "advanced since the last checkpoint, run `/todo save` (or at "
                       "least refresh `--state`) so a future resume stays current; the "
                       "compaction summary is stashed in `/todo %s history`." % (seq, seq))
        # Under-reconciled digest gets ONE line, in the same rail as the hook-health
        # and memo nags. Self-capping: the gate file fingerprints the state already
        # reported, so this stays silent until it CHANGES (or an --apply re-arms it) —
        # a nag that fires every session is one you learn to ignore. Fail-open: a
        # broken heal scan must never break a session start.
        try:
            heal_nag = _heal.nag(task)
        except Exception:
            heal_nag = None
        if heal_nag:
            msg.append(heal_nag)
        if hh_nag:
            msg.append(hh_nag)
        print("\n".join(msg))
        return
    opens = [t for t in sorted_tasks() if is_on_board(t)]
    lines = []
    if opens:
        lines.append("[task-station] You have %d open task(s). If the user's request matches one, attach to it "
                     "(full how-to: task-station guidance); otherwise a new task will be tracked "
                     "once the work is clear:" % len(opens))
        for t in opens[:8]:
            lines.append("  - #%s [%s] %s (%s)" % (t.get("seq") or "?", t["id"][:8], t["title"], rel_time(t.get("updated_ts"))))
    if hh_nag:
        lines.append(hh_nag)
    if lines:
        print("\n".join(lines))


# ------------------------------------------------------------------- main ----

def main(argv=None):
    """`argv=None` reads sys.argv, exactly as before. The explicit-list form exists so
    a caller already holding this module can run a subcommand through the REAL parser
    and dispatch without paying another interpreter start-up — lib/stop_steps.py runs
    the Stop hook's seven best-effort steps that way."""
    p = argparse.ArgumentParser(prog="task-station")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("create"); sp.add_argument("--session", default=None)
    sp.add_argument("--title", required=True); sp.add_argument("--summary", default="")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like (digest)")
    sp.add_argument("--step", action="append", default=None,
                    help="seed a checklist step (repeatable)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-attach", dest="no_attach", action="store_true",
                    help="create unattached (empty sessions) — /todo <n> -s fresh-starts")
    sp.add_argument("--attach", action="store_true",
                    help="force-bind --session even if it's a substantive tracked session")
    sp.add_argument("--active", action="store_true",
                    help="start the task active (●) instead of the default new (○)")
    sp.set_defaults(fn=cmd_create)

    sp = sub.add_parser("attach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", required=True); sp.add_argument("--color", default=None)
    sp.add_argument("--note", default=None,
                    help="append this text to the task's activity log (fold a prompt in)")
    sp.add_argument("--force-key", dest="force_key", action="store_true",
                    help="confirm an attach whose prompt/--note identity keys "
                         "(PR/work-item #) don't match the target task's (F9 soft-guard)")
    sp.set_defaults(fn=cmd_attach)

    sp = sub.add_parser("detach"); sp.add_argument("--session", required=True)
    sp.add_argument("--task", default=None,
                    help="task to detach from (default: the session's linked task)")
    sp.set_defaults(fn=cmd_detach)

    sp = sub.add_parser("bump"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_bump)

    sp = sub.add_parser("skip"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_skip)

    sp = sub.add_parser("done"); sp.add_argument("--session", default=None)
    sp.add_argument("--task", default=None)   # close any task by seq/id from anywhere
    sp.set_defaults(fn=cmd_done)

    # HARD-delete a task. Hidden (help=SUPPRESS) — not in --help's command list,
    # the config board, or the README; lifecycle is close-not-delete (use `done`).
    # Discoverable only via `guidance`'s maintenance line. See cmd_delete.
    sp = sub.add_parser("delete", help=argparse.SUPPRESS)
    sp.add_argument("--task", required=True)
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("mark-edited"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_mark_edited)   # PostToolUse(Write|Edit|NotebookEdit) one-shot reminder

    sp = sub.add_parser("touch-file"); sp.add_argument("--session", required=True)
    sp.add_argument("--file", dest="file", required=True)
    sp.set_defaults(fn=cmd_touch_file)    # PostToolUse: append an edited path to the task's briefing

    sp = sub.add_parser("board")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="best-effort: open the written board.html in a browser (macOS)")
    sp.add_argument("--refresh-if-live", dest="refresh_if_live", action="store_true",
                    help="Stop-hook path: silently regen board.html only when auto-refresh is on AND the file exists")
    sp.set_defaults(fn=cmd_board)         # /todo board — ONE board; no engine choice

    sp = sub.add_parser("brains")         # Interbrain brains & sharing config (brains.json)
    sp.add_argument("action", nargs="?", default="show",
                    help="list | add | edit | rename | archive | share | unshare | "
                         "assign | suggest | show")
    sp.add_argument("args", nargs="*", help="positional args for the action")
    sp.add_argument("--with", dest="with_", default=None, help="audience for share/unshare")
    sp.add_argument("--tag", default=None, help="optional category tag scope for a share rule")
    sp.add_argument("--task", default=None, help="task ref for `suggest` (the scoring audit)")
    # Definable-brain fields (add/edit) — the auto-attach signals. List fields accept a
    # comma/space-separated value and REPLACE (an empty string clears them).
    sp.add_argument("--description", default=None, help="one-line brain description")
    sp.add_argument("--purpose", default=None, help="what the brain is for")
    sp.add_argument("--keywords", default=None, help="auto-attach keywords (comma/space list)")
    sp.add_argument("--repos", default=None, help="auto-attach repos (comma/space list)")
    sp.add_argument("--category-affinity", dest="category_affinity", default=None,
                    help="auto-attach category tags (comma/space list)")
    sp.set_defaults(fn=cmd_brains)

    sp = sub.add_parser("hook-health",
                        help="failures the (deliberately non-fatal) hooks recorded")
    sp.add_argument("--clear", action="store_true",
                    help="empty the log and re-arm the SessionStart nag")
    sp.set_defaults(fn=cmd_hook_health)

    # heal — the RECONCILE pass: turn the append-only decision log into current state.
    # Per-task by default; a DRY RUN by default. See cmd_heal and lib/heal.py.
    sp = sub.add_parser("heal",
                        help="reconcile a task's append-only decision log into current "
                             "state (dry run by default)")
    sp.add_argument("--session", default=None)
    sp.add_argument("ref", nargs="?", default=None, metavar="TASK",
                    help="the task to reconcile, named POSITIONALLY: `heal --scan 12` is "
                         "exactly `heal --scan --task 12`, resolved by the same lookup. "
                         "It exists because /heal passes $ARGUMENTS straight through, so "
                         "a bare `/heal 12` arrives here as a positional. REFUSED "
                         "alongside --all (they name different scopes) or alongside a "
                         "--task naming a DIFFERENT task; the same ref in both places is "
                         "accepted.")
    sp.add_argument("--task", default=None,
                    help="task to reconcile by seq/id (default: the attached task). May "
                         "also be given positionally — `heal --scan 12`.")
    sp.add_argument("--scan", action="store_true",
                    help="layer 1 ONLY: the deterministic scan. Zero tokens, and it "
                         "never modifies the task.")
    sp.add_argument("--apply", action="store_true",
                    help="perform the mechanical plan. Backs the task blob up first and "
                         "REFUSES if that backup cannot be written. Without this, heal "
                         "is a dry run and changes nothing. Prints ONLY what it did — "
                         "not the scan, the decision list or the judgment list, which "
                         "the dry run already showed you. An --apply that performs at "
                         "least one operation STAMPS the heal; one that performs NONE is "
                         "refused rather than stamping a reconcile that never happened "
                         "(use --mark-healed for a judgement-only pass).")
    sp.add_argument("--verbose", action="store_true",
                    help="with --apply: print the FULL block (scan, current decisions, "
                         "judgment list) as well as what was applied. Off by default "
                         "because that block is ~94%% decision text and the caller has "
                         "just read it in the dry run.")
    sp.add_argument("--mark-healed", dest="mark_healed", action="store_true",
                    help="record a JUDGEMENT-ONLY heal: the log was read and nothing "
                         "needed changing. Performs no operation, backs the blob up "
                         "first, and is the only way to say so — without it the record "
                         "still reads `last heal never` and every session opens on a "
                         "false alarm.")
    sp.add_argument("--note", default=None, metavar="WHY",
                    help="with --mark-healed: one line saying what was checked and why "
                         "nothing changed (stored on the task, shown by the scan)")
    sp.add_argument("--dispose-acks", dest="dispose_acks", default=None,
                    metavar="ID8,…|all",
                    help="retro-fill the disposition of acks recorded before one was "
                         "required (needs --apply to write). Takes memo id8s or `all` — "
                         "`all` is legitimate here, since those acking sessions no "
                         "longer exist. Pass exactly ONE of --decision/--memory/--noop. "
                         "Every retro-fill is MARKED retro with who filled it and when, "
                         "the original ack's session/timestamp are never rewritten, and "
                         "a disposition the acker chose is never overwritten.")
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="with --dispose-acks: the memo became a decision (optional TEXT "
                         "says which). Records the disposition only — a heal never mints "
                         "a decision dated to a session that no longer exists.")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="with --dispose-acks: it was folded into that agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="with --dispose-acks: no durable change was needed — the reason "
                         "is MANDATORY and is recorded on the ledger")
    sp.add_argument("--all", dest="all", action="store_true",
                    help="sweep every open/active task instead of one — warns about its "
                         "scope before doing anything")
    sp.add_argument("--split", type=int, default=None, metavar="N",
                    help="mark decision N as SPLIT into the decisions named by --into "
                         "(add those first with `update --decision`)")
    sp.add_argument("--merge", default=None, metavar="N1,N2,…",
                    help="mark these decisions as MERGED into the one named by --into "
                         "(add that summary first with `update --decision`)")
    sp.add_argument("--into", default=None, metavar="N1,N2,…",
                    help="the decision(s) a --split became, or the ONE that a --merge "
                         "was absorbed into")
    sp.set_defaults(fn=cmd_heal)

    sp = sub.add_parser("stop-gate"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_gate)     # Stop hook: block ending an untracked edit session

    sp = sub.add_parser("post-compact"); sp.add_argument("--session", required=True)
    sp.add_argument("--trigger", default="")
    sp.set_defaults(fn=cmd_post_compact)  # PostCompact hook: stash the compaction summary to history (stdin)

    sp = sub.add_parser("stop-nudge"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_stop_nudge)    # Stop hook: non-blocking staleness nudge (opt-in auto-checkpoint)

    sp = sub.add_parser("render"); sp.add_argument("--session", required=True)
    sp.add_argument("--arg", default="")
    sp.add_argument("--format", choices=["ascii", "md"], default="ascii",
                    help="list output format: ascii (default) or md (GitHub tables, printed verbatim)")
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("add-project"); sp.add_argument("--task", required=True)
    sp.add_argument("--project", required=True); sp.set_defaults(fn=cmd_add_project)

    # search — ranked cross-task FTS search (tier-1 hit list) + --detail digest.
    sp = sub.add_parser("search")
    sp.add_argument("terms", nargs="*", help="terms to search task text for")
    sp.add_argument("--session", default=None)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--open", dest="open", action="store_true",
                   help="only open + active tasks")
    g.add_argument("--closed", dest="closed", action="store_true",
                   help="only closed tasks")
    g.add_argument("--all", dest="all", action="store_true",
                   help="all tasks (the default)")
    sp.add_argument("--detail", default=None,
                    help="print one task's full digest (read-only) by seq/id instead of searching")
    sp.set_defaults(fn=cmd_search)

    # add-cost — accumulate a delegate run's worker cost onto a task (called by
    # delegate.py so per-run cost lands on the linked /todo task, not just workers.json).
    sp = sub.add_parser("add-cost"); sp.add_argument("--task", required=True)
    sp.add_argument("--usd", required=True, help="this run's total_cost_usd")
    # Optional per-run detail — when any is given, a record is appended to task['runs'].
    sp.add_argument("--model", default=None, help="model id this run used (e.g. claude-opus-4-8)")
    sp.add_argument("--session", default=None, help="worker session id for this run")
    sp.add_argument("--seq-label", dest="seq_label", default=None,
                    help="concurrent-worker label discriminator for this run")
    sp.add_argument("--usage-json", dest="usage_json", default=None,
                    help='JSON token usage {in,out,cache_read,cache_creation}')
    sp.add_argument("--category", default="real", choices=["real", "wasted"],
                    help="real (successful run, default) | wasted (crashed/timed-out spend)")
    sp.set_defaults(fn=cmd_add_cost)

    # add-event — append one entry to a task's bounded event feed (delta-brief source).
    # Quiet bookkeeping called by delegate.py so worker/child milestones land on the
    # linked /todo task (no attach, no activity-log entry, like add-cost).
    sp = sub.add_parser("add-event"); sp.add_argument("--task", required=True)
    sp.add_argument("--kind", required=True,
                    help="event kind: log|decision|milestone|summary|status|run|worker|child")
    sp.add_argument("--text", default="",
                    help="event text (truncated to %d chars)" % EVENT_TEXT_MAX)
    sp.add_argument("--session", default=None, help="session id to attribute the event to")
    sp.set_defaults(fn=cmd_add_event)

    # add-ledger — append a hub<->worker interaction to a task's provenance ledger
    # (unbounded append-only; delegate.py posts spawn/resume/stop/adopt/finish/crash).
    sp = sub.add_parser("add-ledger", help="append a hub<->worker interaction to a task's provenance ledger")
    sp.add_argument("--task", required=True)
    sp.add_argument("--action", required=True,
                    choices=["spawn", "resume", "iterate", "modify", "stop",
                             "adopt", "finish", "crash", "timeout", "stalled"])
    sp.add_argument("--worker", default=None, help="worker session uuid")
    sp.add_argument("--session", default=None, help="acting HUB session uuid")
    sp.add_argument("--detail", default=None)
    sp.set_defaults(fn=cmd_add_ledger)

    # register-worker-session — roster a worker session on a task record (name/model/
    # harness/status). Quiet bookkeeping; delegate.py posts it on spawn + terminal.
    sp = sub.add_parser("register-worker-session",
                        help="roster a worker session on a task record (#463)")
    sp.add_argument("--task", required=True)
    sp.add_argument("--session", required=True, help="worker session uuid")
    sp.add_argument("--name", default=None, help="worker display slug")
    sp.add_argument("--model", default=None)
    sp.add_argument("--harness", default="claude")
    sp.add_argument("--status", default="running")
    sp.set_defaults(fn=cmd_register_worker)

    # memo — hand a fact/decision to a task's working session(s). One subcommand
    # (send|ack|show); a shared, visible ack ledger lets multiple sessions on one task
    # coordinate without double-implementing. --task accepts any seq/id-prefix.
    sp = sub.add_parser("memo")
    sp.add_argument("sub", choices=["send", "ack", "show"], help="memo action")
    sp.add_argument("--task", default=None,
                    help="target task (seq or id-prefix); ack/show default to the "
                         "session's attached task")
    sp.add_argument("--text", default="", help="memo body (send)")
    sp.add_argument("--id", default=None, help="memo id-prefix (ack/show)")
    sp.add_argument("--session", default=None,
                    help="acting session id (signs a send; REQUIRED to ack)")
    sp.add_argument("--corrects", action="append", default=None, metavar="TARGET",
                    help="on send: declare what this memo REPLACES (repeatable) — a "
                         "memory-note slug, `decision:<n>` on the target task, or another "
                         "memo's id8. A memo that declares corrections cannot be acked "
                         "without a disposition that engages them.")
    # An ack must carry EXACTLY ONE disposition — a bare ack is an error. An ack is a
    # receipt; treating it as an integration is how a correction never lands.
    sp.add_argument("--decision", nargs="?", const=True, default=None,
                    help="ack disposition: promote the memo to a decision (optional TEXT "
                         "overrides the memo body)")
    sp.add_argument("--memory", default=None, metavar="SLUG",
                    help="ack disposition: record that it was folded into that "
                         "agent-memory note")
    sp.add_argument("--noop", default=None, metavar="REASON",
                    help="ack disposition: no durable change needed — the reason is "
                         "MANDATORY and is recorded on the ledger")
    sp.set_defaults(fn=cmd_memo)

    # F5 correspondence: link a peer pair · fork a peer node into my own task ·
    # subscribe to a peer's feed (mints memos when it advances).
    sp = sub.add_parser("link")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_link)

    sp = sub.add_parser("fork")
    sp.add_argument("--from", dest="from_ref", required=True,
                    help="peer task ref to fork <alias>-<n|uuid8>")
    sp.add_argument("--title", default=None, help="title for my forked task (default: peer's)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_fork)

    sp = sub.add_parser("subscribe")
    sp.add_argument("--task", required=True, help="my task (seq or id-prefix)")
    sp.add_argument("--peer", required=True, help="peer task ref <alias>-<n|uuid8>")
    sp.add_argument("--on", dest="on", default="checkpoint,decision,trail",
                    help="event kinds to watch (comma list: checkpoint,decision,trail)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscribe)

    sp = sub.add_parser("subscriptions")
    sp.add_argument("sub", nargs="?", default="check",
                    help="check (diff peer feeds, mint memos) | list")
    sp.add_argument("--throttle", action="store_true",
                    help="hook path: self-throttle + stay silent (skip if run recently)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_subscriptions)

    # F6 PostToolUse artifact capture — scans a tool RESULT (stdin) for PR/work-item URLs.
    sp = sub.add_parser("capture-artifacts")
    sp.add_argument("--session", required=True)
    sp.add_argument("--text", default=None,
                    help="text to scan (default: read the tool result from stdin)")
    sp.set_defaults(fn=cmd_capture_artifacts)

    sp = sub.add_parser("status"); sp.add_argument("--task", required=True)
    sp.add_argument("value", nargs="?", default=None,
                    help="new|active to set (new = the stored open); omit to report the "
                         "current status (close via /done)")
    sp.add_argument("--session", default=None, help="session id to attribute the transition to")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("session-title"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_title)

    sp = sub.add_parser("whoami"); sp.add_argument("--session", required=True)
    sp.add_argument("--porcelain", action="store_true",
                    help="print only the attached task's seq (empty if none) for scripts")
    sp.add_argument("--statusline", action="store_true",
                    help="print a colored '#seq <dot> [TAG] title' status-bar segment (empty if no task)")
    sp.add_argument("--width", type=int, default=0,
                    help="with --statusline, truncate the title so the segment fits N columns (0 = no limit)")
    sp.set_defaults(fn=cmd_whoami)

    sp = sub.add_parser("update"); sp.add_argument("--task", required=True)
    sp.add_argument("--title", default=None); sp.add_argument("--summary", default=None)
    sp.add_argument("--append-summary", dest="append_summary", default=None)
    sp.add_argument("--restore-summary", dest="restore_summary", nargs="?", const="",
                    default=None, metavar="N",
                    help="bring back a PRESERVED previous summary — bare restores the "
                         "most recent, `<n>` an older one (1-based, as numbered by "
                         "`/todo <n> history`). `--summary` replaces wholesale, so the "
                         "text it overwrites is kept append-only; this is the inverse "
                         "that makes the replace safe. The restore is itself reversible: "
                         "the text it replaces is preserved too, and nothing is deleted.")
    sp.add_argument("--state", default=None,
                    help="set the briefing's 'where it stands / next step' line "
                         "(model-curated; '' clears it)")
    sp.add_argument("--goal", default=None,
                    help="one line: what 'done' looks like ('' clears it)")
    sp.add_argument("--step-add", dest="step_add", action="append", default=None,
                    help="append a checklist step (repeatable)")
    sp.add_argument("--step-done", dest="step_done", action="append", type=int, default=None,
                    metavar="N", help="tick step N (1-based; repeatable)")
    sp.add_argument("--step-undone", dest="step_undone", action="append", type=int, default=None,
                    metavar="N", help="untick step N (1-based; repeatable)")
    sp.add_argument("--step-supersede", dest="step_supersede", action="append", type=int,
                    default=None, metavar="N",
                    help="retire STALE step N from the checklist (1-based; repeatable). "
                         "The checklist's one reconcile verb, shaped like --supersedes: "
                         "non-destructive, so the step keeps its text in `/todo <n> "
                         "history` marked with what replaced it, and it counts in NEITHER "
                         "side of the n/m progress number. A --step-add in the same "
                         "update is recorded as the replacement. There is deliberately no "
                         "--step-edit: supersede the stale step and add a corrected one.")
    sp.add_argument("--step-restore", dest="step_restore", action="append", type=int,
                    default=None, metavar="N",
                    help="UNDO --step-supersede on step N (1-based; repeatable) — it "
                         "returns to the active checklist with its text and tick intact")
    sp.add_argument("--decision", action="append", default=None,
                    help="append a decision note (repeatable, append-only). Every "
                         "still-current decision renders in the digest — there is no age "
                         "or count limit. Past %d chars you get an advisory suggesting "
                         "`heal --split`; it never refuses, the entry is stored in full."
                         % _dec.LONG_DECISION_CHARS)
    sp.add_argument("--supersedes", action="append", type=int, default=None, metavar="N",
                    help="mark decision N (1-based, as numbered by `/todo <n> history`) as "
                         "REPLACED by the --decision in this same update; repeatable, so one "
                         "decision may replace several. A superseded decision vanishes from "
                         "the default digest and every other present-tense surface, and "
                         "survives only in `history`, marked with its replacement.")
    sp.add_argument("--pin", action="store_true", default=False,
                    help="pin the --decision in this same update. A pin is READING ORDER, "
                         "not visibility: every still-current decision renders in the "
                         "digest anyway, and pinned ones sort FIRST (marked ★) as the "
                         "architecture spine, ahead of everything else oldest-first. No "
                         "limit on how many are pinned.")
    sp.add_argument("--pin-decision", dest="pin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="pin EXISTING decision N (1-based; repeatable) — sorts it into "
                         "the digest's leading spine block")
    sp.add_argument("--unpin-decision", dest="unpin_decision", action="append", type=int,
                    default=None, metavar="N",
                    help="unpin existing decision N (1-based; repeatable) — it returns to "
                         "the oldest-first narrative block; it does NOT stop rendering")
    sp.add_argument("--restore-decision", dest="restore_decision", action="append",
                    type=int, default=None, metavar="N",
                    help="UNDO the reconcile mark on decision N (1-based; repeatable): "
                         "clears a supersede, split or merge and returns it to the "
                         "default digest. The inverse of all three verbs — nothing was "
                         "ever deleted, so any heal is reversible.")
    sp.add_argument("--log", action="append", default=None,
                    help="append a dated milestone/finding to the task's history "
                         "(repeatable, append-only). Off the default resume path — "
                         "surfaced only by `/todo <n> history`.")
    sp.add_argument("--pr", action="append", default=None,
                    help="store a PR URL on the task (repeatable, upsert by url)")
    sp.add_argument("--pr-desc", dest="pr_desc", default=None,
                    help="description for the --pr url in this update "
                         "(or the most-recent stored pr when no --pr is given)")
    sp.add_argument("--story", action="append", default=None,
                    help="store a story/work-item URL on the task (repeatable, upsert by url)")
    sp.add_argument("--story-desc", dest="story_desc", default=None,
                    help="description for the --story url in this update "
                         "(or the most-recent stored story when no --story is given)")
    sp.add_argument("--color", default=None); sp.add_argument("--effort", default=None)
    sp.add_argument("--trail-visibility", dest="trail_visibility", default=None,
                    choices=["private", "checkpoints", "full"],
                    help="F5: how much of this task's trail its feed exports — private "
                         "(default, trails never leave), checkpoints (digest only), full "
                         "(include the prompt/response trail)")
    sp.add_argument("--relate", action="append", default=None,
                    help="record a relation edge to another task by seq/id (repeatable, "
                         "idempotent). The related task's event feed hears about it too.")
    # The TYPED edge flags. One rule covers all of them: the SUBORDINATE side stores
    # the edge — the dependent, the child, the absorbed task — so every one of these
    # writes on the task being updated, and the reverse direction is derived.
    sp.add_argument("--depends-on", dest="depends_on", action="append", default=None,
                    metavar="TASK",
                    help="THIS task depends on TASK — TASK must land first (repeatable, "
                         "idempotent). Stored on the dependent, which is this task. "
                         "There is no --blocks: that is this edge read backwards, and "
                         "reverse edges are always derived, never stored. Local tasks "
                         "only. A cycle warns and still stores.")
    sp.add_argument("--parent", default=None, metavar="TASK",
                    help="TASK is THIS task's parent — at most ONE, because a task under "
                         "two parents double-counts in every roll-up. Writing a second "
                         "one REPLACES the first and says which it replaced. Stored on "
                         "the child, which is this task. Local tasks only.")
    sp.add_argument("--absorbed-by", dest="absorbed_by", default=None, metavar="TASK",
                    help="THIS task's work became part of TASK, so THIS task CLOSES. "
                         "Absorbing inherits work, so it prints a reconcile handoff for "
                         "TASK — steps are never merged automatically, and children are "
                         "never moved. (Compare --replaces, which closes the OTHER task.)")
    sp.add_argument("--replaces", action="append", default=None, metavar="TASK",
                    help="THIS task replaces TASK, so TASK CLOSES — its approach was "
                         "dropped, not absorbed, so nothing is inherited and no reconcile "
                         "is needed (repeatable). Note the direction: --replaces closes "
                         "the OTHER task, --absorbed-by closes THIS one. Spelled "
                         "`replaces`, not `supersedes`, because --supersedes already "
                         "retires a DECISION and both are valid in one command.")
    sp.add_argument("--duplicates", action="append", default=None, metavar="TASK",
                    help="THIS task and TASK are the same work (repeatable). Symmetric — "
                         "either side may declare it, it is stored once, and the reverse "
                         "reads the same. Closes nothing and decides nothing: it makes "
                         "duplication a warning instead of something someone must notice.")
    sp.add_argument("--unrelate", action="append", default=None, metavar="TASK",
                    help="remove EVERY edge this task stores to TASK, whatever the kind "
                         "(repeatable). An edge states present structure, not a "
                         "historical belief, so it is corrected rather than superseded. "
                         "Removing nothing is reported, not an error. Only touches this "
                         "task's own edges — a derived reverse edge belongs to the task "
                         "that stored it.")
    sp.add_argument("--session", default=None,
                    help="session id to attribute --relate / --summary events to (optional)")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("pin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.add_argument("--new", action="store_true",
                    help="pin a freshly-minted unborn session (claude --session-id <uuid>)")
    sp.set_defaults(fn=cmd_pin)

    sp = sub.add_parser("unpin"); sp.add_argument("--task", required=False, default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_unpin)

    sp = sub.add_parser("prompt-tint"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_tint)

    sp = sub.add_parser("session-tint"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_session_tint)

    sp = sub.add_parser("prompt-title"); sp.add_argument("--session", default=None)
    sp.add_argument("--prompt", default=None); sp.set_defaults(fn=cmd_prompt_title)

    sp = sub.add_parser("prompt-context"); sp.add_argument("--session", required=True)
    sp.set_defaults(fn=cmd_prompt_context)

    sp = sub.add_parser("native")
    sp.set_defaults(fn=cmd_native)        # read-only listing of Claude Code's native task lists

    sp = sub.add_parser("adopt")
    sp.add_argument("--native", required=True,
                    help="native task ref <list-prefix>:<id> to promote into a durable station task")
    sp.set_defaults(fn=cmd_adopt)

    sp = sub.add_parser("guidance")
    sp.set_defaults(fn=cmd_guidance)

    sp = sub.add_parser("session-start"); sp.add_argument("--session", required=True)
    sp.add_argument("--source", default=""); sp.set_defaults(fn=cmd_session_start)

    # sweep-orphans — stop background workers whose spawning hub session is gone.
    # Called from the SessionStart hook; logs each reap to stderr, always exits 0.
    sp = sub.add_parser("sweep-orphans",
                        help="reap task-station workers whose spawning hub is gone")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_sweep_orphans)

    sp = sub.add_parser("repos")
    sp.add_argument("terms", nargs="*",
                    help="terms to rank repos by; omit (or 'show') to print the index. "
                         "Also: include/exclude/enrich <name>, config")
    sp.add_argument("--refresh", action="store_true", help="rescan roots + rewrite the index")
    sp.add_argument("--json", action="store_true", help="emit the structured list for the skill")
    sp.add_argument("--quiet", action="store_true", help="with --refresh, print only a one-line summary")
    sp.add_argument("--no-llm", dest="no_llm", action="store_true",
                    help="with --refresh, skip model enrichment — deterministic summary/keywords only")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="with --refresh, report which enrich:true repos WOULD be sent — send nothing")
    sp.add_argument("--re-summarize", dest="re_summarize", action="store_true",
                    help="with --refresh, regenerate summaries even when one already exists")
    sp.add_argument("--detect-roots", dest="detect_roots", action="store_true",
                    help="propose candidate discovery roots for first-run setup")
    sp.add_argument("--set-roots", dest="set_roots", default=None,
                    help="persist a comma-separated list of discovery roots")
    sp.set_defaults(fn=cmd_repos)

    sp = sub.add_parser("obsidian")
    sp.add_argument("--sync-all", dest="sync_all", action="store_true",
                    help="(re)export every task into the configured Obsidian vault")
    sp.add_argument("--flush", dest="flush", action="store_true",
                    help="re-export ONLY the pending-resync (previously-failed) tasks "
                         "and clear their flags — cheaper than --sync-all; run from an "
                         "unsandboxed shell to drain a sandboxed-export backlog")
    sp.add_argument("--quiet", dest="quiet", action="store_true",
                    help="with --flush: suppress happy-path output (used by the hooks)")
    sp.add_argument("--status", dest="status", action="store_true",
                    help="report the Obsidian export status (default when no flag given)")
    sp.set_defaults(fn=cmd_obsidian)

    sp = sub.add_parser("usage")
    sp.add_argument("mode", nargs="?", default=None,
                    choices=["scan-all", "import-costbar"],
                    help="scan-all: ledger every transcript · import-costbar: one-time costbar cache import")
    sp.add_argument("--task", default=None)
    sp.add_argument("--refresh", action="store_true")
    sp.add_argument("--flush", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--path", default=None,
                    help="with import-costbar: path to session_totals.json (default: ~/.claude/cache/)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_usage)         # WS1 usage ledger: per-task model mix + derived $

    sp = sub.add_parser("export")         # WS8 generic episodic export → any directory
    sp.add_argument("--dir", default=None, help="destination directory (created if absent)")
    sp.add_argument("--task", default=None, help="export one task (seq/id)")
    sp.add_argument("--all", dest="all", action="store_true", help="export every task (default)")
    sp.add_argument("--status", default=None, choices=["open", "active", "closed", "new"],
                    help="export only tasks in this status")
    sp.add_argument("--include", default=None,
                    help="sections to render: usage,prompts,history (default usage,history)")
    sp.add_argument("--since", default=None, help="only tasks updated at/after this ISO date")
    sp.add_argument("--prune", dest="prune", action="store_true",
                    help="reconcile --dir against live tasks: remove notes whose task "
                         "no longer exists (or was redacted) + update index.md")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("sessions")       # WS5 live-session viewer: running Claude processes
    sp.add_argument("--task", default=None,
                    help="filter to one task's live sessions (seq/id)")
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.set_defaults(fn=cmd_sessions)

    sp = sub.add_parser("prompts")        # WS6 tasks-by-prompt view: the exact prompt trail
    sp.add_argument("--task", default=None)
    sp.add_argument("--json", dest="as_json", action="store_true")
    sp.add_argument("--md", dest="as_md", action="store_true",
                    help="the shareable Markdown artifact (full text + timestamps)")
    sp.add_argument("--all", action="store_true",
                    help="the complete RAW trail (every kind: commands, compaction rows, "
                         "wrappers) with no replies; default is human prompts + Claude's reply")
    sp.set_defaults(fn=cmd_prompts)

    sp = sub.add_parser("config")
    _add_config_args(sp)
    sp.set_defaults(fn=lambda a: __import__("config").cmd_config(a))

    sp = sub.add_parser("glossary")       # WS3 per-task canonical vocabulary
    _add_glossary_args(sp)
    sp.set_defaults(fn=cmd_glossary)

    sp = sub.add_parser("brief")          # WS3 deterministic house-style brief
    _add_brief_args(sp)
    sp.set_defaults(fn=cmd_brief)

    sp = sub.add_parser("recap")          # task 444: private weekly usage recap
    sp.add_argument("--week", default=None, metavar="YYYY-Www",
                    help="the ISO week to summarize (default: the current week)")
    sp.add_argument("--open", dest="open", action="store_true",
                    help="open the rendered recap in your browser (macOS)")
    sp.add_argument("--json", dest="as_json", action="store_true",
                    help="print the privacy-safe aggregate stats instead of the path")
    sp.add_argument("--no-scan", dest="no_scan", action="store_true",
                    help="skip the pre-render ledger scan (use the stored numbers as-is)")
    sp.add_argument("--auto-if-due", dest="auto_if_due", action="store_true",
                    help=argparse.SUPPRESS)   # hook entry point: gated + silent
    sp.add_argument("--quiet", dest="quiet", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(fn=cmd_recap)

    sp = sub.add_parser("glossary-context")   # WS3 adapter hook: inject the block
    sp.add_argument("--task", default=None)
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_glossary_context)

    sp = sub.add_parser("stream")         # A-2 durable JSONL event ledger
    sp.add_argument("--since", default=None,
                    help="read events after this cursor (0-based global index)")
    sp.add_argument("--tail", nargs="?", type=int, const=20, default=None,
                    metavar="N", help="the last N events (default 20)")
    sp.add_argument("--json", action="store_true", help="emit raw JSONL envelopes")
    sp.add_argument("--backfill", action="store_true",
                    help="emit a task.snapshot per still-unstreamed task (idempotent)")
    sp.add_argument("--verify", action="store_true",
                    help="check per-task n continuity + shard order")
    sp.set_defaults(fn=cmd_stream)

    sp = sub.add_parser("redact",          # right-to-be-forgotten
                        help="scrub a task's payloads from the stream ledger")
    sp.add_argument("--task", required=True, help="task to redact (seq/id)")
    sp.add_argument("--session", default=None)
    sp.set_defaults(fn=cmd_redact)

    a = p.parse_args(argv)
    a.fn(a)


def _add_glossary_args(sp):
    """Attach the glossary command's args to a parser/subparser. Shared by main()'s
    `glossary` subcommand AND the `/todo glossary` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="list",
                    help="list | add | edit | rm | <task#> (list another task)")
    sp.add_argument("args", nargs="*",
                    help='positional term fields: add "<name>" <layer> <state> "<def>"')
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--layer", default=None)
    sp.add_argument("--state", default=None)
    sp.add_argument("--def", dest="definition", default=None)
    sp.add_argument("--rename", default=None, help="edit: set a new canonical name")


def _add_brief_args(sp):
    """Attach the brief command's args to a parser/subparser. Shared by main()'s
    `brief` subcommand AND the `/todo brief` dispatch, so the two stay identical."""
    sp.add_argument("action", nargs="?", default="render",
                    help="render | path — render (default) templates a brief-spec JSON; "
                         "path creates + records the output path for a model-authored brief")
    sp.add_argument("--task", default=None, help="target task (seq/id); else the session's task")
    sp.add_argument("--session", default=None)
    sp.add_argument("--spec", default=None, help="brief-spec JSON file (default: read stdin)")


def _add_config_args(sp):
    """Attach the config command's flags to a parser/subparser. Shared by main()'s
    `config` subcommand AND the `/todo config` dispatch (which parses the tokens
    after the keyword with the SAME spec), so the two stay identical."""
    sp.add_argument("--workspace-dirs", dest="workspace_dirs", default=None)
    sp.add_argument("--workspace-dirs-get", dest="workspace_dirs_get", action="store_true")
    sp.add_argument("--artifacts-root", dest="artifacts_root", nargs="?", const="", default=None,
                    help="root dir for rendered /brief artifacts (default: <data_dir>/artifacts; "
                         "TASK_STATION_ARTIFACTS_ROOT env wins; no value clears the override)")
    sp.add_argument("--artifacts-root-get", dest="artifacts_root_get", action="store_true")
    sp.add_argument("--category-pack", dest="category_pack", nargs="*", default=None,
                    help="(no arg / 'list') list packs + active · <name> select the active pack "
                         "(dev · finance · hr · exec · general + org packs; per-slot overrides still win)")
    sp.add_argument("--category-pack-get", dest="category_pack_get", action="store_true")
    sp.add_argument("--categories", dest="categories", nargs="*", default=None,
                    help="(no arg) show enabled set + toggles · 'edit' print config path")
    sp.add_argument("--enable", dest="enable", default=None,
                    help="enable a category slot (key, emoji, or [TAG])")
    sp.add_argument("--disable", dest="disable", default=None,
                    help="disable a category slot (refuses ⚫ GENERAL — permanent)")
    sp.add_argument("--auto-categories", dest="auto_categories", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="auto-enable a category slot the first time a task is assigned to it (default on)")
    sp.add_argument("--auto-categories-get", dest="auto_categories_get", action="store_true")
    sp.add_argument("--bare-cmds", dest="bare_cmds", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--bare-cmds-get", dest="bare_cmds_get", action="store_true")
    sp.add_argument("--update-check", dest="update_check", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--update-check-get", dest="update_check_get", action="store_true")
    sp.add_argument("--stream", dest="stream", nargs="?", choices=["on","off"], const="on", default=None,
                    help="the durable JSONL event ledger (internal, local-only; default on)")
    sp.add_argument("--stream-get", dest="stream_get", action="store_true")
    sp.add_argument("--stream-dir", dest="stream_dir", default=None,
                    help="external tee directory for the stream ('' clears it; default off)")
    sp.add_argument("--stream-dir-get", dest="stream_dir_get", action="store_true")
    sp.add_argument("--board-autorefresh", dest="board_autorefresh", nargs="?", choices=["on","off"], const="on", default=None,
                    help="open /todo board tab stays live via meta-refresh + Stop-hook regen (no network); default off")
    sp.add_argument("--board-autorefresh-get", dest="board_autorefresh_get", action="store_true")
    sp.add_argument("--done-closes-window", dest="done_closes_window", nargs="?", choices=["on","off"], const="on", default=None,
                    help="auto-close the terminal window ~1s after a no-arg /done closes this session's task; opt-in, default off (window stays open)")
    sp.add_argument("--done-closes-window-get", dest="done_closes_window_get", action="store_true")
    sp.add_argument("--board-browser", dest="board_browser", nargs="?", const="", default=None,
                    help='browser app the board opens in (macOS: open -a "<App>", e.g. "Google '
                         'Chrome"); no value clears it (back to the system default browser)')
    sp.add_argument("--board-browser-get", dest="board_browser_get", action="store_true")
    sp.add_argument("--interbrain", dest="interbrain", nargs="?", choices=["on", "off", "auto"],
                    const="on", default=None,
                    help="board Interbrain federation: on · off · auto (default auto → on when >1 brain/peers)")
    sp.add_argument("--interbrain-get", dest="interbrain_get", action="store_true")
    sp.add_argument("--knowledge-plane", dest="knowledge_plane", nargs="?",
                    choices=["on", "off", "auto"], const="on", default=None,
                    help="board knowledge plane: on · off · auto — the vault's notes as a "
                         "second plane above the task plane, read-only (default auto → on "
                         "when a configured vault holds at least one note)")
    sp.add_argument("--knowledge-plane-get", dest="knowledge_plane_get", action="store_true")
    sp.add_argument("--org-label", dest="org_label", nargs="?", const="", default=None,
                    help='display label for the org brain (default "Org brain"; e.g. "Company Brain"); no value clears it')
    sp.add_argument("--org-label-get", dest="org_label_get", action="store_true")
    # RETIRED (#444): there is one board now, so there is nothing to select. Still PARSED —
    # and answered with a one-line notice — so muscle memory and old scripts get an
    # explanation instead of an argparse error. Hidden from --help.
    sp.add_argument("--board-engine", dest="board_engine", nargs="?", const="",
                    default=None, help=argparse.SUPPRESS)
    sp.add_argument("--board-engine-get", dest="board_engine_get", action="store_true",
                    help=argparse.SUPPRESS)
    sp.add_argument("--theme", dest="theme", nargs="*", default=None,
                    help="(no arg) list themes + active · <name> select · save <name> · edit · preview")
    sp.add_argument("--tint-theme", dest="tint_theme", nargs="?", choices=["auto","dark","light"], const="auto", default=None,
                    help="appearance variant: auto follows the OS (dark=Dark Sands, light=Light Sands), or force dark/light")
    sp.add_argument("--tint-theme-get", dest="tint_theme_get", action="store_true")
    sp.add_argument("--tint", dest="tint", nargs="?", choices=["on","off"], const="on", default=None,
                    help="full-palette terminal tint via escape codes (default on; TASK_STATION_TINT env overrides)")
    sp.add_argument("--tint-get", dest="tint_get", action="store_true")
    sp.add_argument("--reset", dest="reset", nargs="?", const="ask", default=None,
                    help="reset ALL config settings to factory defaults — asks to confirm (tasks unaffected)")
    sp.add_argument("--title", dest="title", nargs="?", choices=["on","off"], const="on", default=None)
    sp.add_argument("--title-get", dest="title_get", action="store_true")
    sp.add_argument("--strict-delegation", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) a managed delegation-rules block in CLAUDE.md")
    # Hidden back-compat alias for the former flag name; same dest.
    sp.add_argument("--policy", dest="strict_delegation", nargs="?",
                    choices=["on", "off"], const="on", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--guaranteed-tracking", dest="guaranteed_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="hook-side deterministic create+attach of a provisional task on a fresh session (default off)")
    sp.add_argument("--guaranteed-tracking-get", dest="guaranteed_tracking_get", action="store_true")
    sp.add_argument("--auto-checkpoint", dest="auto_checkpoint", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="opt-in automatic checkpointing: a PostCompact hook stashes the "
                         "compaction summary into the task history (free), plus a "
                         "post-compaction + a staleness nudge keep the digest fresh (default off)")
    sp.add_argument("--auto-checkpoint-get", dest="auto_checkpoint_get", action="store_true")
    sp.add_argument("--checkpoint-at", dest="checkpoint_at", nargs="?", const="off", default=None,
                    metavar="TOKENS",
                    help="LEGACY/fallback proactive threshold (estimated tokens): with "
                         "--auto-checkpoint on, prompt a full /todo save before auto-compaction "
                         "once the transcript-size ESTIMATE grows past this (default off; prefer "
                         "--checkpoint-pct; 0/off disables it, PostCompact stash still runs)")
    sp.add_argument("--checkpoint-at-get", dest="checkpoint_at_get", action="store_true")
    sp.add_argument("--checkpoint-pct", dest="checkpoint_pct", nargs="?", const="off", default=None,
                    metavar="PCT",
                    help="proactive context-pressure threshold as a %% of --context-window, "
                         "MEASURED from the transcript's real usage block: with --auto-checkpoint "
                         "on, prompt a full /todo save before auto-compaction once measured context "
                         "reaches this %% (default 65; 1-95; 0/off disables)")
    sp.add_argument("--checkpoint-pct-get", dest="checkpoint_pct_get", action="store_true")
    sp.add_argument("--context-window", dest="context_window", nargs="?", default=None,
                    metavar="TOKENS",
                    help="the model's context-window size, the denominator --checkpoint-pct "
                         "measures against (default 200000; raise for a larger window)")
    sp.add_argument("--context-window-get", dest="context_window_get", action="store_true")
    sp.add_argument("--checkpoint-milestone-edits", dest="checkpoint_milestone_edits",
                    nargs="?", const="off", default=None, metavar="COUNT",
                    help="with --auto-checkpoint on, fire the light staleness nudge only after "
                         "this many meaningful events (edits / promotions) since the last digest "
                         "refresh (default 5; 0/off = nudge on any staleness)")
    sp.add_argument("--checkpoint-milestone-edits-get", dest="checkpoint_milestone_edits_get",
                    action="store_true")
    sp.add_argument("--desktop-bridge", dest="desktop_bridge", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="wire the dependency-free MCP server into Claude Desktop (on) / remove it (off)")
    sp.add_argument("--statusline", dest="statusline", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="install (on) / remove (off) the opt-in self-sufficient status bar; "
                         "non-destructive, never clobbers an existing statusLine (default off)")
    sp.add_argument("--statusline-get", dest="statusline_get", action="store_true")
    sp.add_argument("--hud", dest="hud", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="install (on) / remove (off) the opt-in cost HUD (turn/session/"
                         "limit/week/total/task $ rows) on the status-bar host; "
                         "non-destructive, priced by the shared usage ledger (default off)")
    sp.add_argument("--hud-get", dest="hud_get", action="store_true")
    sp.add_argument("--hud-rows", dest="hud_rows", nargs="?", const="", default=None,
                    metavar="ROWS",
                    help="comma-separated cost-HUD rows to show, in order (subset of "
                         "turn,session,limits,week,total,task; default all)")
    sp.add_argument("--hud-rows-get", dest="hud_rows_get", action="store_true")
    sp.add_argument("--hud-eco", dest="hud_eco", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="append the eco-footprint column to the cost HUD (default off)")
    sp.add_argument("--hud-eco-get", dest="hud_eco_get", action="store_true")
    sp.add_argument("--ultracode-hints", dest="ultracode_hints", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="suggest ultracode multi-agent breadth on fan-out-worthy tasks "
                         "(L/XL, or RESEARCH/REVIEW/DATA at M+) for read/think phases "
                         "only — never repo writes (default on)")
    sp.add_argument("--ultracode-hints-get", dest="ultracode_hints_get", action="store_true")
    sp.add_argument("--notify", dest="notify", nargs="?", choices=["on", "off"], const="on", default=None,
                    help="macOS banner when a delegated worker run finishes/fails (default off; "
                         "TASK_STATION_NOTIFY env overrides)")
    sp.add_argument("--notify-get", dest="notify_get", action="store_true")
    sp.add_argument("--delegate-bypass-permissions", dest="delegate_bypass_permissions",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="spawn --bg workers in a worktree with bypassPermissions so they "
                         "never block (default on, enforced worktree-only; "
                         "TASK_STATION_DELEGATE_BYPASS env overrides)")
    sp.add_argument("--delegate-bypass-permissions-get",
                    dest="delegate_bypass_permissions_get", action="store_true")
    sp.add_argument("--reap-workers-on-done", dest="reap_workers_on_done",
                    nargs="?", choices=["on", "off"], const="on", default=None,
                    help="stop this task's live --bg workers when it closes so they don't "
                         "linger/respawn in Agent View (default on; airtight — only a "
                         "registered, role==worker, task-station-named, idle worker is "
                         "reaped; TASK_STATION_REAP_WORKERS_ON_DONE env overrides)")
    sp.add_argument("--reap-workers-on-done-get",
                    dest="reap_workers_on_done_get", action="store_true")
    sp.add_argument("--notify-webhook", dest="notify_webhook", nargs="?", const="", default=None,
                    metavar="URL",
                    help="POST worker finished/failed events to this URL (Slack/Teams/ntfy-style "
                         "JSON receiver); no value clears it (TASK_STATION_NOTIFY_WEBHOOK overrides)")
    sp.add_argument("--notify-webhook-get", dest="notify_webhook_get", action="store_true")
    sp.add_argument("--obsidian-vault", dest="obsidian_vault", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="export tasks (one-way) into this Obsidian vault; files land under "
                         "<vault>/task-station/. No value turns export OFF "
                         '(e.g. --obsidian-vault "~/Documents/Obsidian Vault")')
    sp.add_argument("--obsidian-vault-get", dest="obsidian_vault_get", action="store_true")
    sp.add_argument("--obsidian-sandbox", dest="obsidian_sandbox", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="add (on) / remove (off) the configured vault in the Claude Code "
                         "sandbox write-allowlist (sandbox.filesystem.allowWrite in your "
                         "settings.json) so in-session exports into a protected folder "
                         "(~/Documents, iCloud) write instantly; does NOT force sandbox on")
    sp.add_argument("--obsidian-sandbox-get", dest="obsidian_sandbox_get", action="store_true")
    sp.add_argument("--obsidian-daily-note", dest="obsidian_daily_note", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="append a line to the vault daily note on task close + /todo save (default off)")
    sp.add_argument("--obsidian-daily-note-get", dest="obsidian_daily_note_get", action="store_true")
    sp.add_argument("--obsidian-daily-heading", dest="obsidian_daily_heading", nargs="?",
                    const="", default=None, metavar="HEADING",
                    help='daily-note heading the entries go under (default "## Claude sessions"); '
                         "no value restores the default")
    sp.add_argument("--obsidian-daily-heading-get", dest="obsidian_daily_heading_get", action="store_true")
    sp.add_argument("--obsidian-prompts", dest="obsidian_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="write the full prompt trail (## Prompts) into exported vault notes "
                         "(default off — prompt export is opt-in; TASK_STATION_OBSIDIAN_PROMPTS overrides)")
    sp.add_argument("--obsidian-prompts-get", dest="obsidian_prompts_get", action="store_true")
    sp.add_argument("--obsidian-category-hubs", dest="obsidian_category_hubs", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="cluster the export/vault graph by category (default ON): a "
                         "[[categories/<slug>]] link in each note + a hub page per category "
                         "under <target>/categories/. Off drops the link and prunes the hub "
                         "pages on the next sync. TASK_STATION_OBSIDIAN_CATEGORY_HUBS overrides")
    sp.add_argument("--obsidian-category-hubs-get", dest="obsidian_category_hubs_get", action="store_true")
    sp.add_argument("--obsidian-subgroups", dest="obsidian_subgroups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="emergent sub-groups within a category (default ON, nested inside "
                         "--obsidian-category-hubs): distinctive recurring title tokens auto-cluster "
                         "into nested categories/<cat-slug>/<token>.md sub-hub pages, and member notes "
                         "link the sub-hub instead of the bare category. Off prunes the sub-hubs and "
                         "reverts members on the next sync. TASK_STATION_OBSIDIAN_SUBGROUPS overrides")
    sp.add_argument("--obsidian-subgroups-get", dest="obsidian_subgroups_get", action="store_true")
    sp.add_argument("--obsidian-story-groups", dest="obsidian_story_groups", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="story hubs (default ON, nested inside --obsidian-category-hubs): tasks that "
                         "share a story id (from the structured `stories` field, referenced by >= 1 "
                         "tasks) get a cross-category stories/<id>.md hub + a [[stories/<id>]] link in "
                         "each member note, IN ADDITION to the category link. Off prunes the hubs and "
                         "drops the link. TASK_STATION_OBSIDIAN_STORY_GROUPS overrides")
    sp.add_argument("--obsidian-story-groups-get", dest="obsidian_story_groups_get", action="store_true")
    sp.add_argument("--knowledge-graph", dest="knowledge_graph", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="second-brain tier (default off): task<->note co-citation edges in the "
                         "board mini-graph + 'Related knowledge' panel, and ## Related wikilink "
                         "emission into the Obsidian mirror. Inert without an --obsidian-vault; "
                         "TASK_STATION_KNOWLEDGE_GRAPH overrides")
    sp.add_argument("--knowledge-graph-get", dest="knowledge_graph_get", action="store_true")
    sp.add_argument("--owner", dest="owner", nargs="?", const="", default=None,
                    metavar="HANDLE",
                    help="owner handle for a SHARED vault: notes nest under <target>/<owner>/ "
                         "and carry the handle (frontmatter/manifest/daily lines); no value "
                         "clears it (single-owner). Run `obsidian --sync-all` after to "
                         "relocate existing notes. TASK_STATION_OWNER overrides")
    sp.add_argument("--owner-get", dest="owner_get", action="store_true")
    sp.add_argument("--usage-tracking", dest="usage_tracking", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="track per-task model usage + derived $ from your local transcripts "
                         "(default on; reads only local files; TASK_STATION_USAGE_TRACKING overrides)")
    sp.add_argument("--usage-tracking-get", dest="usage_tracking_get", action="store_true")
    sp.add_argument("--usage-prompts", dest="usage_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="capture prompt text into the usage ledger (same-machine; default on)")
    sp.add_argument("--usage-prompts-get", dest="usage_prompts_get", action="store_true")
    sp.add_argument("--board-prompts", dest="board_prompts", nargs="?",
                    choices=["on", "off"], const="on", default=None,
                    help="show the captured prompt trail on the visual board "
                         "(local-only; default on)")
    sp.add_argument("--board-prompts-get", dest="board_prompts_get", action="store_true")
    sp.add_argument("--usage-billing-mode", dest="usage_billing_mode", nargs="?",
                    choices=["api", "subscription"], const="api", default=None,
                    help="frame the derived $ as metered (api) or flat-rate API-equivalent value "
                         "(subscription); default api")
    sp.add_argument("--usage-billing-mode-get", dest="usage_billing_mode_get", action="store_true")
    sp.add_argument("--recap", dest="recap", nargs="?", choices=["on", "off"],
                    const="on", default=None,
                    help="auto-generate the private weekly usage recap under <data_dir>/recaps/ "
                         "(local-only, never synced; default off; TASK_STATION_RECAP overrides)")
    sp.add_argument("--recap-get", dest="recap_get", action="store_true")
    sp.add_argument("--recap-curator-cmd", dest="recap_curator_cmd", nargs="?", const="", default=None,
                    help="command that turns recap AGGREGATE stats (JSON on stdin; never prompt "
                         "text) into 3 tailored tips; no value clears it (default off)")
    sp.add_argument("--recap-curator-cmd-get", dest="recap_curator_cmd_get", action="store_true")
    sp.add_argument("--editor-scheme", dest="editor_scheme", nargs="?", const="", default=None,
                    help="URI scheme the board uses to open file paths, e.g. cursor/vscode/zed "
                         "→ <scheme>://file/<abs>, or `file` → file://<abs>; no value AUTO-DETECTS "
                         "from your editor ($VISUAL/$EDITOR, then installed editor apps, else file)")
    sp.add_argument("--editor-scheme-get", dest="editor_scheme_get", action="store_true")


if __name__ == "__main__":
    main()
