"""Shared constants + late-binding facade access for the board seams.

The engine split (Phase 2) moves task-station.py's top-level constants here and
gives every split module ONE way to read the facade's live globals: `g("NAME")`.
The facade (`lib/task-station.py`) keeps the config block — BASE/DATA/STORE/
TASKS_DIR/LINKS_DIR/PENDING_BRIEFS/DELEGATE_REGISTRY/_LIVE_BG_INDEX/
PROJECTS_ROOT — plus every name the test suite patches (`ts.<name> = …`), and
calls `bind(globals())` before the star-imports so `g` resolves against THAT
module copy. 77 test files load the engine as a fresh module copy by literal
path; each copy purges `board.*` from sys.modules first, so each generation of
split modules binds its own facade.

NEVER call `g()` at module level (import time) — only inside a def.
"""
import os
import re
import time
from datetime import datetime

_G = None


def bind(g):            # facade calls this before the star-imports
    global _G
    _G = g


def g(name):            # late-bound read of the facade's live namespace
    return _G[name]


def set_g(name, value):  # write-back for rebinding routed globals
    _G[name] = value


__all__ = [
    # --- mechanism ---
    "bind", "g", "set_g",
    # --- optional categories plugin ---
    "cats",
    # --- constants (original engine order) ---
    "LOG_KEEP", "EVENTS_KEEP", "EVENT_TEXT_MAX", "DECISION_PIN_MARK",
    "DECISION_DEAD_MARK", "ACTIVITY_TAIL", "FILES_KEEP", "NUDGE_PROMPT_MAX",
    "NUDGE_ESCALATE_AFTER", "SKIP_SENTINEL", "MAX_CLOSED_IN_LIST",
    "SUBSTANCE_FLOOR", "IDLE_GAP_CAP", "SPANS_KEEP", "SEARCH_SCAN_LIMIT",
    "SEARCH_HITS_SHOWN", "DELTA_MAX_ITEMS", "DELTA_MAX_CHARS",
    "MEMOS_KEEP", "MEMOS_HARD_CAP", "MEMO_PENDING_MAX", "MEMO_LINE_MAX",
    "CORRECTION_PATTERNS", "CORRECTION_NOUN_PATTERNS",
    "STATUS_OPEN", "STATUS_ACTIVE", "STATUS_CLOSED", "STATUS_DEFAULT",
    "STATUS_BOARD", "STATUS_SETTABLE", "STATUS_GLYPH", "STATUS_GLYPH_CLOSED",
    "STATUS_DISPLAY", "STATUS_INPUT_ALIASES",
    "EFFORT_ORDER", "_EFFORT_SLOTS", "EFFORT_GAUGE", "EFFORT_GAUGE_EMPTY",
    "EFFORT_WORD", "_EFFORT_ALIASES",
    "FANOUT_CATEGORIES", "_FANOUT_ANY_MIN", "_FANOUT_BREADTH_MIN",
    "_ULTRACODE_RE", "_COMMANDS_HELP", "_COMMANDS_LEGEND",
    "STOP_GATE_MAX_BLOCKS", "_ORDINAL_REF_RE", "_DEDUP_STOPWORDS", "_PR_WORDS",
    "MEMO_DISPOSITIONS", "MEMO_DISPOSITION_HELP", "RUNS_CAP",
    "CACHE_DIR", "MSGCOUNT_CACHE_FILE", "MSGCOUNT_CACHE_MAX",
    "MSGCOUNT_CACHE_TOUCH", "SESSION_PATH_MEM_MAX",
    "_USAGE_TAIL_BYTES", "_CONTEXT_USAGE_KEYS", "_MODEL_FAMILIES",
    "_REL_KIND_RANK", "_REL_KIND_RANK_UNKNOWN", "_SEMANTIC_WEIGHTS",
    "_REL_LINE_WORDS", "_REL_LINE_DEFAULT", "_REL_LINE_LABELS",
    "_REL_LINE_LABELS_DEFAULT", "ORPHAN_SWEEP_GRACE_SECS",
    "SESSION_END_AGENTS_TIMEOUT", "SESSION_END_REASON_MAX",
    "STATION_WATCHED_FILES", "_MD_HEADER", "_PR_URL_RE", "_LOCAL_ONLY_KINDS",
    "_FOREIGN_HANDLE_RE", "DEFAULT_CLOSED_LIST", "_NO_TASK_ATTACHED",
    "_BULLET_RE", "_BOARD_VER_RE", "_OWNER_FALLBACK_COLOR",
    "_SUBS_CHECK_INTERVAL",
    # --- utility defs ---
    "_now", "_owner", "rel_time", "_norm_tokens", "_norm_nums",
    "_slug", "_project_slug", "_task_slug", "brief_output_path",
]


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

# Categories / colours are an OPTIONAL plugin: all of that logic lives in
# categories.py. If it's absent (or fails to import), `cats` is None and the
# tracker runs plain and colourless — no tags, no --color, no tint hints. See
# categories.py and CATEGORIES.md.
try:
    import categories as cats
except Exception:
    cats = None


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


def _now():
    return time.time()


def _owner():
    """The configured shared-vault owner handle, or None when unset (single-owner,
    BYTE-IDENTICAL to today). Cheap config read on the mutation path."""
    try:
        import config
        return config.owner() or None
    except Exception:
        return None


# -- edited / blocked markers: the "real work happened" enforcement signal -----
# A session that has EDITED a file but has no attached task is doing untracked
# work. `.edited` records that an edit happened; `.blocked` counts how many times
# the Stop gate has refused to let the turn end, so a non-complying loop can't
# wedge the session (we give up after STOP_GATE_MAX_BLOCKS).
STOP_GATE_MAX_BLOCKS = 2


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


_PR_WORDS = {"pr", "pull", "pullrequest", "pullrequests"}


# The three ways an ack may DISPOSE of a memo. A bare ack is no longer one of them:
# an ack is a receipt, and a receipt was mistaken for an integration once already.
MEMO_DISPOSITIONS = ("decision", "memory", "noop")
MEMO_DISPOSITION_HELP = (
    "an ack must say what it DID with the memo — pass exactly one of:\n"
    "  --decision [TEXT]   promote it to a decision on this task\n"
    "  --memory <slug>     record that it was folded into that agent-memory note\n"
    "  --noop \"<reason>\"   no durable change needed (the reason is mandatory)")


RUNS_CAP = 50   # per-run records kept on a task (most-recent), so a long-lived task can't grow unbounded


CACHE_DIR = "cache"                  # <data_dir>/cache — resolved per call (tests repoint DATA)
MSGCOUNT_CACHE_FILE = "msgcounts.json"
MSGCOUNT_CACHE_MAX = 4000            # entries kept on disk; least-recently-used dropped past this
MSGCOUNT_CACHE_TOUCH = 86400         # refresh an entry's last-used stamp at most once a day
SESSION_PATH_MEM_MAX = 8192          # resolved transcript paths kept, same reasoning


# How much of the transcript tail measure_context_tokens reads. The live context
# size lives on the LAST assistant message's usage block, which is near the end of
# the file, so a bounded tail read keeps this cheap even on a multi-MB transcript.
_USAGE_TAIL_BYTES = 256 * 1024
# The usage fields that make up the RESIDENT context (what's actually re-sent to the
# model each turn). output_tokens is deliberately EXCLUDED — generated output isn't
# part of the next turn's context window.
_CONTEXT_USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


_MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")


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


# Signal → edge-weight for `touches-same`: a shared PR is the strongest same-work
# signal (same change), a shared story or file next, a shared repo/project the
# weakest (mere co-location). An edge's weight sums the weights of every signal the
# two tasks share, so "same PR + same files" outranks "same repo".
_SEMANTIC_WEIGHTS = {"pr": 3, "story": 2, "file": 2, "repo": 1}


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


# Workers younger than this are skipped: a just-spawned worker's hub may not have its
# session file on disk yet, which would make a live hub look dead.
ORPHAN_SWEEP_GRACE_SECS = 120


# `claude agents --json` is the one subprocess this pass can need. harness's own
# adapter allows it 20s, which would blow the budget on a wedged CLI; 5s is long
# enough for a healthy call and short enough to lose gracefully — a timeout returns
# {}, which every caller reads as "unknown" and therefore reaps nothing, leaving the
# work to the SessionStart sweep.
SESSION_END_AGENTS_TIMEOUT = 5
SESSION_END_REASON_MAX = 40      # a reason is a code word; anything longer is not one


# The data-dir files the station itself READS. The manifest's FileChanged matcher is
# basename-level, so any project's `config.json` fires this hook; the data-dir test in
# cmd_file_changed is what makes it OURS, and this set is what makes the record honest.
STATION_WATCHED_FILES = ("config.json", "categories.json", "repos.json",
                         "brains.json", "workers.json")


_MD_HEADER = ("|  | # | Task | Category | Effort | Activity |\n"
              "|:-:|--:|------|----------|--------|----------|")


# GitHub `…/pull/<n>` and Azure DevOps `…/pullrequest/<n>` (dev.azure.com and the
# generic `_git/<repo>/pullrequest/<n>` form). Path chars stay URL-safe so a
# trailing `.`/`)`/space in a note never gets swallowed; `\d+` bounds the tail.
_PR_URL_RE = re.compile(
    r'https://github\.com/[\w.-]+/[\w.-]+/pull/\d+'
    r'|https://dev\.azure\.com/[\w%./+-]+?/pullrequest/\d+'
    r'|https://[\w.-]+/[\w%./+-]+?/_git/[\w%./+-]+?/pullrequest/\d+',
    re.IGNORECASE,
)


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


DEFAULT_CLOSED_LIST = 20  # how many closed tasks `/todo closed` (no count) shows


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


_BULLET_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+")


_BOARD_VER_RE = re.compile(r'name="ts-board-version" content="([0-9]+\.[0-9]+\.[0-9]+)"')


_OWNER_FALLBACK_COLOR = "#7f8a9c"


_SUBS_CHECK_INTERVAL = 120     # seconds between throttled (hook-path) checks
